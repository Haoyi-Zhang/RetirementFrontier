// Read an assembled, disposable checkpoint. No writes or application replay.
#include <rocksdb/db.h>
#include <rocksdb/metadata.h>
#include <rocksdb/version.h>

#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

namespace r = ROCKSDB_NAMESPACE;

std::string hex(r::Slice s) {
    constexpr char digits[] = "0123456789abcdef";
    std::string out;
    for (unsigned char c : s.ToString()) {
        out += digits[c >> 4];
        out += digits[c & 15];
    }
    return out;
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    r::DB* raw = nullptr;
    r::Options options;
    auto status = r::DB::OpenForReadOnly(options, argv[1], &raw);
    std::unique_ptr<r::DB> db(raw);
    if (!status.ok()) {
        std::cout << "open-failed\n";
        std::cerr << status.ToString() << '\n';
        return 0;
    }
    std::vector<r::LiveFileMetaData> tables;
    db->GetLiveFilesMetaData(&tables);
    std::unique_ptr<r::Iterator> it(db->NewIterator(r::ReadOptions{}));
    std::string rows;
    for (it->SeekToFirst(); it->Valid(); it->Next())
        rows += "row\t" + hex(it->key()) + "\t" + hex(it->value()) + "\n";
    if (!it->status().ok()) {
        std::cout << "read-failed\n";
        std::cerr << it->status().ToString() << '\n';
        return 0;
    }
    std::cout << "ok\nengine\t" << ROCKSDB_MAJOR << '.' << ROCKSDB_MINOR << '.'
              << ROCKSDB_PATCH << "\nsequence\t" << db->GetLatestSequenceNumber() << '\n';
    for (const auto& table : tables)
        std::cout << "file\t" << std::filesystem::path(table.name).filename().string()
                  << '\t' << table.size << '\n';
    std::cout << rows;
}
