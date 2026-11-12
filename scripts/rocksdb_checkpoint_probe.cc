// Ordinary checkpoint creation, reopening, and disposal in a disposable store.
#include <rocksdb/db.h>
#include <rocksdb/metadata.h>
#include <rocksdb/utilities/checkpoint.h>
#include <rocksdb/version.h>
#include <rocksdb/write_batch.h>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;
namespace r = ROCKSDB_NAMESPACE;
using Records = std::map<std::string, std::string>;
using Files = std::map<std::string, std::uintmax_t>;
constexpr int keys_per_range = 32;

void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}

void check(const r::Status& status) {
  if (!status.ok()) throw std::runtime_error(status.ToString());
}

std::string input(const char* path) {
  std::ifstream file(path, std::ios::binary);
  require(file.is_open(), "cannot open public input");
  std::ostringstream bytes;
  bytes << file.rdbuf();
  auto result = bytes.str();
  require(!result.empty(), "empty public input");
  return result;
}

void flush_group(r::DB& db, const std::string& prefix, const std::string& value,
                 Records& expected) {
  r::WriteBatch batch;
  for (int i = 0; i < keys_per_range; ++i) {
    auto key = prefix + std::to_string(1000 + i);
    auto payload = value + std::to_string(i);
    check(batch.Put(key, payload));
    expected[key] = std::move(payload);
  }
  r::WriteOptions write;
  write.sync = true;
  check(db.Write(write, &batch));
  r::FlushOptions flush;
  flush.wait = true;
  check(db.Flush(flush));
}

void checkpoint(r::DB& db, const fs::path& where) {
  require(!fs::exists(where), "checkpoint destination already exists");
  r::Checkpoint* raw = nullptr;
  check(r::Checkpoint::Create(&db, &raw));
  std::unique_ptr<r::Checkpoint> snapshot(raw);
  check(snapshot->CreateCheckpoint(where.string()));
}

void compact(r::DB& db, const char* first, const char* last) {
  r::Slice lower(first), upper(last);
  check(db.CompactRange(r::CompactRangeOptions{}, &lower, &upper));
}

Files verify(const fs::path& location, const Records& expected, std::uint64_t seq) {
  r::DB* raw = nullptr;
  r::Options options;
  check(r::DB::OpenForReadOnly(options, location.string(), &raw));
  std::unique_ptr<r::DB> db(raw);
  require(db->GetLatestSequenceNumber() == seq, "checkpoint client sequence changed");
  std::unique_ptr<r::Iterator> it(db->NewIterator(r::ReadOptions{}));
  it->SeekToFirst();
  for (const auto& [key, value] : expected) {
    require(it->Valid() && it->key().ToString() == key && it->value().ToString() == value,
            "checkpoint recovered a different key or value");
    it->Next();
  }
  check(it->status());
  require(!it->Valid(), "checkpoint recovered extra keys");
  std::vector<r::LiveFileMetaData> rows;
  db->GetLiveFilesMetaData(&rows);
  Files files;
  for (const auto& row : rows) {
    const auto name = fs::path(row.name).filename().string();
    require(name.size() > 4 && name.substr(name.size() - 4) == ".sst" &&
                name.substr(0, name.size() - 4).find_first_not_of("0123456789") ==
                    std::string::npos,
            "unexpected SST name");
    const auto size = fs::file_size(location / name);
    require(size == row.size && files.emplace(name, size).second,
            "missing, duplicate, or inconsistent live table");
  }
  return files;
}

void emit(const char* label, const Files& files) {
  std::cout << "\"" << label << "\":{";
  const char* separator = "";
  for (const auto& [name, size] : files) {
    std::cout << separator << "\"" << name << "\":" << size;
    separator = ",";
  }
  std::cout << "}";
}

int main(int argc, char** argv) try {
  require(argc == 4 || (argc == 5 && std::string(argv[4]) == "--retain-staging-copy"),
          "expected a fresh staging directory, two public inputs and optional copy flag");
  const fs::path root(argv[1]);
  require(fs::is_directory(root) && fs::is_empty(root), "expected an empty staging directory");
  const auto live = root / "database", archived = root / "archived";
  const auto before = root / "before", after = root / "after";
  const auto first = input(argv[2]), second = input(argv[3]);
  r::Options options;
  options.create_if_missing = true;
  options.error_if_exists = true;
  options.disable_auto_compactions = true;
  options.compression = r::kNoCompression;
  options.db_host_id = "retirementfrontier";
  r::DB* raw = nullptr;
  check(r::DB::Open(options, live.string(), &raw));
  std::unique_ptr<r::DB> db(raw);
  Records expected;
  flush_group(*db, "a", first, expected);
  flush_group(*db, "a", second, expected);
  flush_group(*db, "y", second, expected);
  flush_group(*db, "y", first, expected);
  const auto sequence = db->GetLatestSequenceNumber();
  require(sequence == 4 * keys_per_range, "unexpected client sequence");
  checkpoint(*db, archived);
  compact(*db, "a", "b");
  checkpoint(*db, before);
  compact(*db, "y", "z");
  checkpoint(*db, after);
  check(db->Close());
  db.reset();

  const auto a = verify(archived, expected, sequence);
  const auto b = verify(before, expected, sequence);
  const auto c = verify(after, expected, sequence);
  const auto final_live = verify(live, expected, sequence);
  require(final_live == c, "the last checkpoint and the live database differ");
  const std::vector<std::pair<fs::path, Files>> views{
      {archived, a}, {before, b}, {after, c}, {live, final_live}};
  for (std::size_t i = 0; i < views.size(); ++i)
    for (std::size_t j = i + 1; j < views.size(); ++j)
      for (const auto& [name, size] : views[i].second)
        for (const auto& [other, other_size] : views[j].second)
          require(fs::equivalent(views[i].first / name, views[j].first / other) ==
                          (name == other) &&
                      (name != other || size == other_size),
                  "native file names disagree with filesystem identity");
  int unique = 0;
  for (const auto& [name, size] : a) {
    if (!b.count(name) && !c.count(name)) {
      require(!fs::exists(live / name) && fs::hard_link_count(archived / name) == 1,
              "archived-only SST has another filesystem link");
      if (argc == 5) {
        const auto copy = root / "expired-tables";
        fs::create_directory(copy);
        require(fs::copy_file(archived / name, copy / name), "staging copy failed");
      }
      ++unique;
    }
  }
  require(unique > 0, "archive contributed no reclaimable SSTs");
  require(fs::remove_all(archived) > 0 && !fs::exists(archived),
          "archive disposal failed");
  require(verify(before, expected, sequence) == b && verify(after, expected, sequence) == c,
          "surviving checkpoint changed after archive disposal");
  std::cout << "{\"rocksdb\":\"" << ROCKSDB_MAJOR << "." << ROCKSDB_MINOR << "."
            << ROCKSDB_PATCH << "\",\"sequence\":" << sequence << ",\"verified_keys\":"
            << expected.size() << ",\"archived_unlinked\":" << unique
            << ",\"post_disposal_reopens\":2,";
  emit("archive", a);
  std::cout << ",";
  emit("before", b);
  std::cout << ",";
  emit("after", c);
  std::cout << "}\n";
} catch (const std::exception& error) {
  std::cerr << error.what() << "\n";
  return 1;
}
