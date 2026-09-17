/**
 * pdfjs-dist >= 5.4 uses the TC39 "upsert" Map methods
 * (getOrInsert / getOrInsertComputed), which only very recent engines
 * ship. Spec-compliant shims keep the viewer working on browsers a few
 * versions back. Imported first from main.tsx.
 */

interface UpsertMap<K, V> extends Map<K, V> {
  getOrInsert(key: K, defaultValue: V): V;
  getOrInsertComputed(key: K, callback: (key: K) => V): V;
}

const proto = Map.prototype as UpsertMap<unknown, unknown>;

if (typeof proto.getOrInsert !== 'function') {
  proto.getOrInsert = function (key, defaultValue) {
    if (!this.has(key)) this.set(key, defaultValue);
    return this.get(key);
  };
}

if (typeof proto.getOrInsertComputed !== 'function') {
  proto.getOrInsertComputed = function (key, callback) {
    if (!this.has(key)) this.set(key, callback(key));
    return this.get(key);
  };
}

export {};
