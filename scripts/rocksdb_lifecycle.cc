// Observe ordinary flush, compaction, and native reopen in an owned database.
#include <rocksdb/db.h>
#include <rocksdb/metadata.h>
#include <rocksdb/version.h>
#include <rocksdb/write_batch.h>

#include <algorithm>
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

void require(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

void check(const rocksdb::Status& status) {
  require(status.ok(), status.ToString());
}

std::string input(const char* path) {
  std::ifstream file(path, std::ios::binary);
  require(file.is_open(), "cannot read public input");
  std::ostringstream bytes;
  bytes << file.rdbuf();
  require(!bytes.str().empty(), "empty public input");
  return bytes.str();
}

using Map = std::map<std::string, std::string>;
using Files = std::map<std::string, uint64_t>;

std::unique_ptr<rocksdb::DB> open(const fs::path& dir, const rocksdb::Options& options,
                                 bool read_only) {
  rocksdb::DB* raw = nullptr;
  check(read_only ? rocksdb::DB::OpenForReadOnly(options, dir.string(), &raw)
                  : rocksdb::DB::Open(options, dir.string(), &raw));
  return std::unique_ptr<rocksdb::DB>(raw);
}

void files(const char* type, const Files& rows) {
  const char* separator = "";
  std::cout << "\"" << type << "\":{";
  for (const auto& [name, size] : rows) {
    require(name.find_first_not_of("0123456789.st") == std::string::npos,
            "unexpected SST filename");
    std::cout << separator << "\"" << name << "\":" << size;
    separator = ",";
  }
  std::cout << "}";
}

void observe(const char* stage, rocksdb::DB& db, const fs::path& dir,
             const Map& expected) {
  rocksdb::ReadOptions read;
  read.verify_checksums = true;
  std::unique_ptr<rocksdb::Iterator> cursor(db.NewIterator(read));
  auto next = expected.begin();
  for (cursor->SeekToFirst(); cursor->Valid(); cursor->Next(), ++next) {
    require(next != expected.end() && cursor->key().ToString() == next->first &&
                cursor->value().ToString() == next->second,
            "native iteration returned a different key or value");
  }
  check(cursor->status());
  require(next == expected.end(), "native iteration omitted keys");

  Files live, disk;
  std::vector<rocksdb::LiveFileMetaData> metadata;
  db.GetLiveFilesMetaData(&metadata);
  for (const auto& file : metadata) {
    const std::string name = fs::path(file.name).filename().string();
    require(live.emplace(name, file.size).second, "duplicate live table");
  }
  for (const auto& file : fs::directory_iterator(dir)) {
    if (file.is_regular_file() && file.path().extension() == ".sst")
      disk.emplace(file.path().filename().string(), file.file_size());
  }
  for (const auto& [name, size] : live)
    require(disk.count(name) && disk.at(name) == size, "live table missing on disk");
  std::ifstream current(dir / "CURRENT");
  std::string manifest;
  require(static_cast<bool>(std::getline(current, manifest)) &&
              manifest.rfind("MANIFEST-", 0) == 0 && manifest.size() > 9 &&
              manifest.find_first_not_of("0123456789", 9) == std::string::npos,
          "unexpected CURRENT contents");
  std::cout << "{\"stage\":\"" << stage << "\",\"sequence\":"
            << db.GetLatestSequenceNumber() << ",\"verified_keys\":"
            << expected.size() << ",\"manifest\":\"" << manifest
            << "\",\"manifest_bytes\":" << fs::file_size(dir / manifest) << ",";
  files("live", live);
  std::cout << ",";
  files("disk", disk);
  std::cout << "}\n";
}

int main(int argc, char** argv) {
  try {
    require(argc == 6, "expected new database path, batch size, and three input files");
    const fs::path dir(argv[1]);
    require(!fs::exists(dir), "database path must not exist");
    const int batch_size = std::stoi(argv[2]);
    require(batch_size >= 4 && batch_size <= 256 && batch_size % 2 == 0,
            "batch size must be even and between 4 and 256");
    const std::vector<std::string> corpus{input(argv[3]), input(argv[4]), input(argv[5])};
    std::cout << "{\"rocksdb\":\"" << ROCKSDB_MAJOR << "." << ROCKSDB_MINOR << "."
              << ROCKSDB_PATCH << "\"}\n";
    rocksdb::Options options;
    options.create_if_missing = true;
    options.error_if_exists = true;
    options.disable_auto_compactions = true;
    options.compression = rocksdb::kNoCompression;
    auto db = open(dir, options, false);
    rocksdb::WriteOptions write;
    write.sync = true;
    rocksdb::FlushOptions flush;
    flush.wait = true;
    Map expected;
    for (int batch = 0; batch < 4; ++batch) {
      rocksdb::WriteBatch transaction;
      for (int row = 0; row < batch_size; ++row) {
        const auto key = std::to_string(10000 + batch * (batch_size / 2) + row);
        const auto& value = corpus[(row + batch) % corpus.size()];
        check(transaction.Put(key, value));
        expected[key] = value;
      }
      check(db->Write(write, &transaction));
      check(db->Flush(flush));
    }
    observe("before-live", *db, dir, expected);
    db.reset();
    options.create_if_missing = false;
    options.error_if_exists = false;
    db = open(dir, options, true);
    observe("before-reopen", *db, dir, expected);
    db.reset();
    db = open(dir, options, false);
    observe("before-compaction", *db, dir, expected);
    rocksdb::CompactRangeOptions compact;
    check(db->CompactRange(compact, nullptr, nullptr));
    observe("after-live", *db, dir, expected);
    db.reset();
    db = open(dir, options, true);
    observe("after-reopen", *db, dir, expected);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << "\n";
    return 1;
  }
}
