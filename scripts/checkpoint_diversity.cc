// Native checkpoint correctness under ordinary compaction and staged retention.
#include <rocksdb/db.h>
#include <rocksdb/metadata.h>
#include <rocksdb/utilities/checkpoint.h>
#include <rocksdb/version.h>
#include <rocksdb/write_batch.h>

#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;
namespace r = ROCKSDB_NAMESPACE;
using Records = std::map<std::string, std::string>;
using Tables = std::map<std::string, std::uintmax_t>;

void require(bool ok, const char* reason) {
  if (!ok) throw std::runtime_error(reason);
}
void check(const r::Status& status) {
  if (!status.ok()) throw std::runtime_error(status.ToString());
}
std::unique_ptr<r::DB> open(const fs::path& path, const r::Options& options) {
  r::DB* raw = nullptr;
  check(r::DB::Open(options, path.string(), &raw));
  return std::unique_ptr<r::DB>(raw);
}
void snapshot(r::DB& db, const fs::path& path) {
  r::Checkpoint* raw = nullptr;
  check(r::Checkpoint::Create(&db, &raw));
  std::unique_ptr<r::Checkpoint> checkpoint(raw);
  check(checkpoint->CreateCheckpoint(path.string()));
}
void compact(r::DB& db, const char* first, const char* last) {
  r::Slice lo(first), hi(last);
  check(db.CompactRange(r::CompactRangeOptions{}, &lo, &hi));
}
const char* scan(const fs::path& path, const Records& expected,
                 std::uint64_t sequence, Tables* tables = nullptr) {
  r::DB* raw = nullptr;
  auto status = r::DB::OpenForReadOnly(r::Options{}, path.string(), &raw);
  std::unique_ptr<r::DB> db(raw);
  if (!status.ok()) return "open-failed";
  require(db->GetLatestSequenceNumber() == sequence, "sequence changed");
  std::unique_ptr<r::Iterator> it(db->NewIterator(r::ReadOptions{}));
  it->SeekToFirst();
  for (const auto& [key,value] : expected) {
    if (!it->status().ok()) return "read-failed";
    require(it->Valid() && it->key().ToString()==key &&
            it->value().ToString()==value, "content differs from input oracle");
    it->Next();
  }
  if (!it->status().ok()) return "read-failed";
  require(!it->Valid(), "unexpected extra key");
  if (tables) {
    std::vector<r::LiveFileMetaData> metadata;
    db->GetLiveFilesMetaData(&metadata);
    for (const auto& row : metadata) {
      const auto name = fs::path(row.name).filename().string();
      require(name.ends_with(".sst") && name.size()>4 &&
              name.substr(0,name.size()-4).find_first_not_of("0123456789")==std::string::npos &&
              fs::file_size(path/name)==row.size && tables->emplace(name,row.size).second,
              "invalid native SST inventory");
    }
  }
  return "exact";
}

int main(int argc, char** argv) try {
  require(argc==3, "expected empty run directory and operation stream");
  const fs::path root(argv[1]);
  require(fs::is_directory(root) && fs::is_empty(root), "expected empty run directory");
  std::ifstream stream(argv[2]);
  require(stream.is_open(), "operation stream missing");
  r::Options options;
  options.create_if_missing=true;
  options.error_if_exists=true;
  options.disable_auto_compactions=true;
  options.compression=r::kNoCompression;
  options.db_host_id="retirementfrontier";
  auto db=open(root/"live", options);
  Records expected;
  std::uint64_t operations=0;
  for (int group=0;group<4;++group) {
    r::WriteBatch batch;
    std::string line;
    while (std::getline(stream,line) && line!="flush") {
      const auto split=line.find('\t');
      require(split!=std::string::npos && split>=2, "invalid operation");
      const auto key=line.substr(1,split-1);
      if (line[0]=='+') {
        auto value=line.substr(split+1);
        check(batch.Put(key,value));
        expected[key]=std::move(value);
      } else {
        require(line[0]=='-' && split+1==line.size() && expected.erase(key)==1,
                "invalid delete");
        check(batch.Delete(key));
      }
      ++operations;
    }
    require(line=="flush" && batch.Count()>0, "missing batch");
    r::WriteOptions write;
    write.sync=true;
    check(db->Write(write,&batch));
    r::FlushOptions flush;
    flush.wait=true;
    check(db->Flush(flush));
  }
  require(stream.peek()==EOF && db->GetLatestSequenceNumber()==operations &&
          !expected.empty(), "unexpected operation count");
  const std::vector<std::string> views{"expired","before","after"};
  snapshot(*db,root/views[0]);
  compact(*db,"a","b");
  snapshot(*db,root/views[1]);
  compact(*db,"y","z");
  snapshot(*db,root/views[2]);
  check(db->Close());
  db.reset();

  std::map<std::string,Tables> required;
  std::map<std::string,fs::path> source;
  Tables catalog;
  std::cout<<"engine\t"<<ROCKSDB_MAJOR<<'.'<<ROCKSDB_MINOR<<'.'<<ROCKSDB_PATCH
           <<"\nlogical\t"<<operations<<'\t'<<expected.size()<<'\n';
  for (const auto& view : views) {
    auto& tables=required[view];
    require(std::string(scan(root/view,expected,operations,&tables))=="exact" &&
            !tables.empty(), "source checkpoint failed");
    for (const auto& [name,size] : tables) {
      if (catalog.count(name))
        require(catalog.at(name)==size && fs::equivalent(source.at(name),root/view/name),
                "same file name has a different native identity");
      catalog[name]=size;
      source.try_emplace(name,root/view/name);
      std::cout<<"file\t"<<view<<'\t'<<name<<'\t'<<size<<'\n';
    }
  }
  for (const auto& chosen : {"before","after"})
    for (const auto& policy : {"before","after","union","all"}) {
      const auto staged=root/(std::string(chosen)+"-"+policy);
      require(fs::create_directory(staged),"restore already exists");
      for (const auto& entry : fs::directory_iterator(root/chosen)) {
        require(entry.is_regular_file() && !entry.is_symlink(), "unexpected metadata entry");
        if (entry.path().extension()!=".sst")
          fs::copy_file(entry.path(),staged/entry.path().filename());
      }
      std::uintmax_t released=0;
      std::vector<std::string> missing;
      for (const auto& [name,size] : catalog) {
        const std::string p(policy);
        const bool keep=p=="all" || (p=="union" ?
            required.at("before").count(name) || required.at("after").count(name) :
            required.at(p).count(name)!=0);
        if (!keep) released+=size;
        if (required.at(chosen).count(name)) {
          if (keep) fs::copy_file(source.at(name),staged/name);
          else missing.push_back(name);
        }
      }
      const std::string status=scan(staged,expected,operations);
      require((status=="exact")==missing.empty(),"native result disagrees with file inventory");
      std::cout<<"restore\t"<<chosen<<'\t'<<policy<<'\t'<<status<<'\t'<<released;
      for (const auto& name : missing) std::cout<<'\t'<<name;
      std::cout<<'\n';
    }
} catch (const std::exception& error) {
  std::cerr<<error.what()<<'\n';
  return 1;
}
