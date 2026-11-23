import io,struct,zlib,unittest
from unittest.mock import patch
from together.storage.attachment_material import hwp_material_digest,MAX_MATERIAL_BYTES

def record(tag,payload):return struct.pack('<I',tag | len(payload)<<20)+payload

def compressed(raw):
    obj=zlib.compressobj(wbits=-15)
    return obj.compress(raw)+obj.flush()

def streams(cursor=0,metadata=b'author',body=b'full table and deadline 2026-11-30',style=b'font and numbering',image=b'poster image',flags=1,docdata=None):
    header=bytearray(256);header[:17]=b'HWP Document File';header[34:36]=bytes([1,5]);struct.pack_into('<I',header,36,flags)
    if docdata is None:
        items=[struct.pack('<HHI',0x4000+i,6 if i%2 else 7,i) for i in range(8)]
        if metadata!=b'author':items.reverse()
        docdata=bytes.fromhex('1c020100000007020080070208000000')+b''.join(items)
    info=record(16,bytes(14)+struct.pack('<III',cursor,0,0))+record(21,style)+record(27,docdata)
    return {'FileHeader':bytes(header),'DocInfo':compressed(info),'BodyText/Section0':compressed(body),'BinData/BIN0001.png':image,'PrvImage':metadata,'\x05HwpSummaryInformation':metadata,'DocOptions/_LinkDoc':metadata}

class Ole:
    def __init__(self,content):self.content=content
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def get_size(self,path):return len(self.content['/'.join(path) if isinstance(path,list) else path])
    def openstream(self,path):return io.BytesIO(self.content['/'.join(path) if isinstance(path,list) else path])
    def listdir(self):return [key.split('/') for key in self.content]

class AttachmentMaterialTests(unittest.TestCase):
    def digest(self,content):
        with patch('olefile.OleFileIO',return_value=Ole(content)):
            return hwp_material_digest(bytes.fromhex('d0cf11e0a1b11ae1')+bytes(512))

    def test_only_cursor_and_save_metadata_changes_are_equal(self):
        a=self.digest(streams());b=self.digest(streams(cursor=99,metadata=b'new author and preview'))
        self.assertIsNotNone(a);self.assertEqual(a,b)

    def test_changed_body_styles_or_embedded_files_are_preserved(self):
        a=self.digest(streams())
        for changes in [{'body':b'another deadline'},{'style':b'changed numbering'},{'image':b'different poster'}]:
            with self.subTest(changes=changes):self.assertNotEqual(a,self.digest(streams(**changes)))

    def test_encrypted_or_incomplete_documents_are_not_compared(self):
        self.assertIsNone(self.digest(streams(flags=3)))
        doc=streams();doc.pop('DocInfo');self.assertIsNone(self.digest(doc))
        doc=streams();doc['DocInfo']=compressed(b'broken');self.assertIsNone(self.digest(doc))

    def test_unknown_oversized_input_is_kept_without_parsing(self):
        self.assertIsNone(hwp_material_digest(b'%PDF-1.7 not an HWP'))
        with patch('olefile.OleFileIO') as parser:
            self.assertIsNone(hwp_material_digest(bytes(MAX_MATERIAL_BYTES+1)))
            parser.assert_not_called()

    def test_start_numbers_are_material_even_when_saved_cursor_is_not(self):
        a=streams();b=streams();raw=zlib.decompress(b['DocInfo'],-15);raw=raw[:6]+b'\x01'+raw[7:];b['DocInfo']=compressed(raw)
        self.assertNotEqual(self.digest(a),self.digest(b))

    def test_valid_crc_and_length_trailer_is_part_of_supported_hwp_streams(self):
        a=streams();b=streams()
        for name in ['DocInfo','BodyText/Section0']:
            clear=zlib.decompress(b[name],-15)
            b[name]+=struct.pack('<II',zlib.crc32(clear)&0xffffffff,len(clear))
        self.assertEqual(self.digest(a),self.digest(b))

    def test_invalid_compressed_trailer_never_produces_a_content_match(self):
        a=streams();a['DocInfo']+=bytes(8)
        self.assertIsNone(self.digest(a))

    def test_numeric_document_settings_values_are_preserved(self):
        prefix=bytes.fromhex('1c020100000007020080070208000000')
        a=prefix+b''.join(struct.pack('<HHI',0x4000+i,6 if i%2 else 7,i) for i in range(8))
        b=a[:-4]+struct.pack('<I',99)
        self.assertNotEqual(self.digest(streams(docdata=a)),self.digest(streams(docdata=b)))

    def test_unknown_document_data_is_not_discarded(self):
        self.assertNotEqual(self.digest(streams(docdata=b'unknown A')),self.digest(streams(docdata=b'unknown B')))
