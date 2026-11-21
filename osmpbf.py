"""
Minimaler OSM-PBF-Leser ohne externe OSM-Bibliotheken.

Benötigt nur `protobuf` (pip install protobuf) und `numpy`.
Die Nachrichtentypen aus fileformat.proto / osmformat.proto werden zur
Laufzeit aufgebaut, daher ist kein protoc nötig.
"""
import struct
import zlib

import numpy as np
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

F = descriptor_pb2.FieldDescriptorProto
_T = {"int32": F.TYPE_INT32, "int64": F.TYPE_INT64, "uint32": F.TYPE_UINT32,
      "sint64": F.TYPE_SINT64, "bytes": F.TYPE_BYTES, "string": F.TYPE_STRING,
      "msg": F.TYPE_MESSAGE, "enum": F.TYPE_ENUM}

# name: [(feldname, nummer, typ, label('o'|'r'), packed, msgtyp)]
_SPEC = {
    "BlobHeader": [("type", 1, "string", "o"), ("indexdata", 2, "bytes", "o"), ("datasize", 3, "int32", "o")],
    "Blob": [("raw", 1, "bytes", "o"), ("raw_size", 2, "int32", "o"), ("zlib_data", 3, "bytes", "o"),
             ("lzma_data", 4, "bytes", "o"), ("zstd_data", 7, "bytes", "o")],
    "StringTable": [("s", 1, "bytes", "r")],
    "PrimitiveBlock": [("stringtable", 1, "msg", "o", False, "StringTable"),
                       ("primitivegroup", 2, "msg", "r", False, "PrimitiveGroup"),
                       ("granularity", 17, "int32", "o"), ("date_granularity", 18, "int32", "o"),
                       ("lat_offset", 19, "int64", "o"), ("lon_offset", 20, "int64", "o")],
    "PrimitiveGroup": [("nodes", 1, "msg", "r", False, "Node"), ("dense", 2, "msg", "o", False, "DenseNodes"),
                       ("ways", 3, "msg", "r", False, "Way"), ("relations", 4, "msg", "r", False, "Relation")],
    "Node": [("id", 1, "sint64", "o"), ("keys", 2, "uint32", "r", True), ("vals", 3, "uint32", "r", True),
             ("lat", 8, "sint64", "o"), ("lon", 9, "sint64", "o")],
    "DenseNodes": [("id", 1, "sint64", "r", True), ("lat", 8, "sint64", "r", True),
                   ("lon", 9, "sint64", "r", True), ("keys_vals", 10, "int32", "r", True)],
    "Way": [("id", 1, "int64", "o"), ("keys", 2, "uint32", "r", True), ("vals", 3, "uint32", "r", True),
            ("refs", 8, "sint64", "r", True)],
    "Relation": [("id", 1, "int64", "o"), ("keys", 2, "uint32", "r", True), ("vals", 3, "uint32", "r", True),
                 ("roles_sid", 8, "int32", "r", True), ("memids", 9, "sint64", "r", True),
                 ("types", 10, "int32", "r", True)],
}


def _build():
    fd = descriptor_pb2.FileDescriptorProto(name="osmpbf_dyn.proto", package="osmpbf", syntax="proto2")
    for name, fields in _SPEC.items():
        m = fd.message_type.add(name=name)
        for f in fields:
            fname, num, typ, lab = f[:4]
            packed = f[4] if len(f) > 4 else False
            fld = m.field.add(name=fname, number=num, type=_T[typ],
                              label=F.LABEL_REPEATED if lab == "r" else F.LABEL_OPTIONAL)
            if typ == "msg":
                fld.type_name = ".osmpbf." + f[5]
            if packed:
                fld.options.packed = True
    pool = descriptor_pool.DescriptorPool()
    pool.Add(fd)
    get = getattr(message_factory, "GetMessageClass", None)
    out = {}
    for name in _SPEC:
        d = pool.FindMessageTypeByName("osmpbf." + name)
        out[name] = get(d) if get else message_factory.MessageFactory(pool).GetPrototype(d)
    return out


_M = _build()


def blocks(path):
    """Liefert (typ, PrimitiveBlock) für jeden OSMData-Block."""
    with open(path, "rb") as fh:
        while True:
            n = fh.read(4)
            if len(n) < 4:
                return
            hdr = _M["BlobHeader"]()
            hdr.ParseFromString(fh.read(struct.unpack(">I", n)[0]))
            blob = _M["Blob"]()
            blob.ParseFromString(fh.read(hdr.datasize))
            if hdr.type != "OSMData":
                continue
            if blob.HasField("zlib_data"):
                raw = zlib.decompress(blob.zlib_data)
            elif blob.HasField("raw"):
                raw = blob.raw
            else:
                raise ValueError("Nicht unterstützte Kompression (nur zlib/raw)")
            pb = _M["PrimitiveBlock"]()
            pb.ParseFromString(raw)
            yield pb


def tags_of(st, keys, vals):
    return {st[k]: st[v] for k, v in zip(keys, vals)}


def dense_nodes(pb, g):
    """numpy-Arrays (ids, lat, lon) in Grad für einen DenseNodes-Block."""
    d = g.dense
    gran = pb.granularity or 100
    ids = np.cumsum(np.fromiter(d.id, dtype=np.int64, count=len(d.id)))
    lat = (pb.lat_offset + gran * np.cumsum(np.fromiter(d.lat, dtype=np.int64, count=len(d.lat)))) * 1e-9
    lon = (pb.lon_offset + gran * np.cumsum(np.fromiter(d.lon, dtype=np.int64, count=len(d.lon)))) * 1e-9
    return ids, lat, lon


def dense_tags(pb, g):
    """Liefert für getaggte Dense-Nodes (index, tags)."""
    st = pb.stringtable.s
    kv = g.dense.keys_vals
    if not kv:
        return
    i = idx = 0
    n = len(kv)
    while i < n:
        t = {}
        while kv[i] != 0:
            t[st[kv[i]].decode()] = st[kv[i + 1]].decode()
            i += 2
        i += 1
        if t:
            yield idx, t
        idx += 1
