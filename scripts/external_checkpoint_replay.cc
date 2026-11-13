// Replay an external request projection on an owned temporary RocksDB.
#include <rocksdb/db.h>
#include <rocksdb/metadata.h>
#include <rocksdb/utilities/checkpoint.h>
#include <rocksdb/version.h>

#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>

namespace fs = std::filesystem;
namespace r = ROCKSDB_NAMESPACE;
using Tables = std::map<std::string, std::uintmax_t>;
using Records = std::map<std::string, std::string>;

void require(bool ok, const char* msg) {
  if (!ok) throw std::runtime_error(msg);
}
void check(const r::Status& s) {
  if (!s.ok()) throw std::runtime_error(s.ToString());
}
std::pair<std::string, std::string> pair(const std::string& line) {
  const auto tab = line.find('\t');
  require(tab != std::string::npos && tab > 0, "malformed replay input");
  return {line.substr(0, tab), line.substr(tab + 1)};
}
void checkpoint(r::DB& db, const fs::path& path) {
  r::Checkpoint* raw = nullptr;
  check(r::Checkpoint::Create(&db, &raw));
  std::unique_ptr<r::Checkpoint> guard(raw);
  check(guard->CreateCheckpoint(path.string()));
}
std::string scan(const fs::path& path, const Records& expected,
                 std::uint64_t sequence, Tables* tables = nullptr) {
  r::DB* raw = nullptr;
  const auto status = r::DB::OpenForReadOnly(r::Options{}, path.string(), &raw);
  std::unique_ptr<r::DB> db(raw);
  if (!status.ok()) return "open-failed";
  require(db->GetLatestSequenceNumber() == sequence, "wrong restored sequence");
  std::unique_ptr<r::Iterator> it(db->NewIterator(r::ReadOptions{}));
  it->SeekToFirst();
  for (const auto& [key, value] : expected) {
    if (!it->status().ok()) return "read-failed";
    require(it->Valid() && it->key() == r::Slice(key) && it->value() == r::Slice(value),
            "restored key or value differs from trace oracle");
    it->Next();
  }
  if (!it->status().ok()) return "read-failed";
  require(!it->Valid(), "unexpected restored key");
  if (tables) {
    std::vector<r::LiveFileMetaData> metadata;
    db->GetLiveFilesMetaData(&metadata);
    for (const auto& item : metadata) {
      const auto name = fs::path(item.name).filename().string();
      require(name.ends_with(".sst") && fs::file_size(path / name) == item.size &&
              tables->emplace(name, item.size).second, "invalid native SST inventory");
    }
  }
  return "exact";
}

int main(int argc, char** argv) try {
  require(argc == 4, "expected empty output directory, trace and oracle");
  const fs::path root(argv[1]);
  require(fs::is_directory(root) && fs::is_empty(root), "unsafe output directory");
  std::ifstream trace(argv[2]), oracle(argv[3]);
  require(trace.is_open() && oracle.is_open(), "missing trace or oracle");
  Records expected;
  std::string line;
  while (std::getline(oracle, line))
    require(expected.insert(pair(line)).second, "duplicate oracle key");
  r::Options options;
  options.create_if_missing = true;
  options.error_if_exists = true;
  options.disable_auto_compactions = true;
  options.compression = r::kNoCompression;
  options.db_host_id = "retirementfrontier";
  r::DB* raw = nullptr;
  check(r::DB::Open(options, (root / "live").string(), &raw));
  std::unique_ptr<r::DB> db(raw);
  std::uint64_t operations = 0, flushes = 0;
  bool dirty = false;
  while (std::getline(trace, line)) {
    if (line == "flush") {
      require(dirty, "empty flush window");
      check(db->SyncWAL());
      r::FlushOptions flush;
      flush.wait = true;
      check(db->Flush(flush));
      dirty = false;
      ++flushes;
      continue;
    }
    const auto [op, value] = pair(line);
    require(op.size() > 1, "missing key");
    if (op[0] == '+') {
      check(db->Put(r::WriteOptions{}, op.substr(1), value));
    } else {
      require(op[0] == '-' && value.empty(), "invalid delete");
      check(db->Delete(r::WriteOptions{}, op.substr(1)));
    }
    ++operations;
    dirty = true;
  }
  require(flushes == 4 && !dirty && operations == db->GetLatestSequenceNumber(),
          "wrong native sequence or flush schedule");
  checkpoint(*db, root / "before");
  check(db->CompactRange(r::CompactRangeOptions{}, nullptr, nullptr));
  checkpoint(*db, root / "after");
  check(db->Close());
  db.reset();

  std::map<std::string, Tables> required;
  std::map<std::string, fs::path> catalog;
  std::cout << "engine\t" << ROCKSDB_MAJOR << '.' << ROCKSDB_MINOR << '.' << ROCKSDB_PATCH
            << "\nlogical\t" << operations << '\t' << expected.size() << '\n';
  for (const std::string view : {"before", "after"}) {
    auto& tables = required[view];
    require(scan(root / view, expected, operations, &tables) == "exact",
            "untouched native checkpoint failed");
    for (const auto& [name, size] : tables) {
      const auto file = root / view / name;
      const auto [it, added] = catalog.try_emplace(name, file);
      require(added || fs::equivalent(it->second, file), "inconsistent SST identity");
      std::cout << "file\t" << view << '\t' << name << '\t' << size << '\n';
    }
  }
  for (const std::string view : {"before", "after"})
    for (const std::string policy : {"before", "after", "union", "all"}) {
      const auto staged = root / (view + "-" + policy);
      require(fs::create_directory(staged), "restore already exists");
      for (const auto& entry : fs::directory_iterator(root / view)) {
        require(entry.is_regular_file() && !entry.is_symlink(), "unexpected metadata entry");
        if (entry.path().extension() != ".sst")
          fs::copy_file(entry.path(), staged / entry.path().filename());
      }
      std::set<std::string> missing;
      std::uintmax_t released = 0;
      for (const auto& [name, path] : catalog) {
        const bool keep = policy == "union" || policy == "all" ||
                          required.at(policy).contains(name);
        if (!keep) released += fs::file_size(path);
        if (!required.at(view).contains(name)) continue;
        if (keep) fs::copy_file(path, staged / name);
        else missing.insert(name);
      }
      std::cout << "restore\t" << view << '\t' << policy << '\t'
                << scan(staged, expected, operations) << '\t' << released;
      for (const auto& name : missing) std::cout << '\t' << name;
      std::cout << '\n';
    }
} catch (const std::exception& e) {
  std::cerr << e.what() << '\n';
  return 1;
}
