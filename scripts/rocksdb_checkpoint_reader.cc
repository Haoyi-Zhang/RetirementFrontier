// Verify an application-owned staged checkpoint with the native reader.
#include <rocksdb/db.h>
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <string>

namespace r = ROCKSDB_NAMESPACE;

int main(int argc, char** argv) try {
  if (argc != 4) throw std::runtime_error("expected a staged checkpoint and two inputs");
  r::DB* raw = nullptr;
  r::Options options;
  auto status = r::DB::OpenForReadOnly(options, argv[1], &raw);
  std::unique_ptr<r::DB> db(raw);
  if (!status.ok()) {
    std::cout << "open:" << static_cast<int>(status.code()) << "\n";
    return 0;
  }
  std::ifstream first(argv[2], std::ios::binary), second(argv[3], std::ios::binary);
  if (!first || !second) throw std::runtime_error("public input unavailable");
  const std::string a((std::istreambuf_iterator<char>(first)), {});
  const std::string b((std::istreambuf_iterator<char>(second)), {});
  if (db->GetLatestSequenceNumber() != 128 || a.empty() || b.empty())
    throw std::runtime_error("unexpected checkpoint or empty input");
  std::unique_ptr<r::Iterator> it(db->NewIterator(r::ReadOptions{}));
  it->SeekToFirst();
  for (int i = 0; i < 64; ++i) {
    if (!it->status().ok()) {
      std::cout << "read:" << static_cast<int>(it->status().code()) << "\n";
      return 0;
    }
    const std::string key = std::string(i < 32 ? "a" : "y") + std::to_string(1000 + i % 32);
    const std::string value = (i < 32 ? b : a) + std::to_string(i % 32);
    if (!it->Valid() || it->key().ToString() != key || it->value().ToString() != value)
      throw std::runtime_error("checkpoint returned a different logical state");
    it->Next();
  }
  if (!it->status().ok() || it->Valid()) throw std::runtime_error("incomplete checkpoint scan");
  std::cout << "ok:64\n";
} catch (const std::exception& e) {
  std::cerr << e.what() << "\n";
  return 1;
}
