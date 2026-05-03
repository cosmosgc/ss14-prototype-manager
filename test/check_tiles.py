import base64
import struct
import yaml
import zlib

class IgnoreTagsLoader(yaml.SafeLoader):
    pass

def ignore_unknown(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None

IgnoreTagsLoader.add_multi_constructor('!', ignore_unknown)

with open('test/dev_map.yml', 'r', encoding='utf-8') as f:
    doc = yaml.load(f, Loader=IgnoreTagsLoader)

entities = doc.get('entities', [])
for group in entities:
    if group.get('proto') == '':
        for ent in group.get('entities', []):
            for comp in ent.get('components', []):
                if comp.get('type') == 'MapGrid':
                    chunks = comp.get('chunks', {})
                    # Find a chunk with non-zero data
                    for key, chunk in list(chunks.items())[:5]:
                        tiles_b64 = chunk.get('tiles', '')
                        cleaned = ''.join(tiles_b64.strip().split())
                        decoded = base64.b64decode(cleaned)
                        # Check if not all zeros
                        if any(b != 0 for b in decoded[:100]):
                            print(f'Chunk {key}: decoded len={len(decoded)}')
                            print(f'First 50 bytes hex: {decoded[:50].hex()}')
                            
                            # Try different decompression methods
                            # 1. Standard zlib
                            try:
                                decompressed = zlib.decompress(decoded)
                                print(f'zlib decompress: {len(decompressed)} bytes')
                            except Exception as e:
                                print(f'zlib decompress failed: {e}')
                            
                            # 2. Raw deflate
                            try:
                                decompressed = zlib.decompress(decoded, -15)
                                print(f'raw deflate: {len(decompressed)} bytes')
                            except Exception as e:
                                print(f'raw deflate failed: {e}')
                            
                            # 3. Gzip
                            try:
                                decompressed = zlib.decompress(decoded, 15 + 16)
                                print(f'gzip: {len(decompressed)} bytes')
                            except Exception as e:
                                print(f'gzip failed: {e}')
                            
                            # Try interpreting as raw 2-byte tiles
                            print(f'\nInterpreting as 2-byte tiles (little-endian):')
                            for i in range(0, min(20, len(decoded)//2)):
                                tile_id = struct.unpack('<H', decoded[i*2:i*2+2])[0]
                                print(f'  Tile {i}: {tile_id}')
                            break
                    break
            break
    break
