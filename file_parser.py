# -*- coding: utf-8 -*-
"""文件解析公共模块：网页版和飞书版共用"""
import io
import os
import re
import gc
import tempfile
import subprocess


def clean_document_text(raw_text: str) -> str:
    raw_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', "", raw_text)
    raw_text = re.sub(r"([A-Za-z])\n(?=[A-Za-z])", r"\1 ", raw_text)
    raw_text = re.sub(r"([^。！？；：\n])\n", r"\1", raw_text)
    raw_text = re.sub(r"\n{2,}", "\n\n", raw_text)
    raw_text = re.sub(r"\s+", " ", raw_text)
    raw_text = re.sub(r"第\s*\d+\s*页\s*/?\d*", "", raw_text)
    return raw_text.strip()


_ocr_engine = None


def ocr_pdf_stream(pdf_bytes: bytes) -> str:
    """扫描版 PDF OCR（可选，未安装 paddleocr 时返回空）"""
    global _ocr_engine
    import fitz
    try:
        from paddleocr import PaddleOCR
        if _ocr_engine is None:
            print("⚠️初始化OCR引擎")
            _ocr_engine = PaddleOCR(
                use_angle_cls=True,
                lang="ch",
                use_gpu=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_memory_optim=True,
                rec_batch_num=1
            )
    except Exception as e:
        print(f"OCR加载失败：{e}")
        return ""
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        ocr_result = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img_data = pix.tobytes("png")
            pix = None
            res = _ocr_engine.ocr(img_data, cls=True)
            lines = []
            if res and res[0]:
                for line_info in res[0]:
                    lines.append(line_info[1][0])
            ocr_result.append("\n".join(lines))
        return clean_document_text("\n\n".join(ocr_result))
    finally:
        if doc is not None:
            doc.close()
        fitz.TOOLS.store_shrink(100)
        gc.collect()


def extract_pdf_bytes_text(file_bytes: bytes) -> str:
    import fitz
    doc = None
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        full_text = ""
        for page in doc:
            blocks = page.get_text("blocks", sort=True)
            page_text = "\n".join([b[4].strip() for b in blocks if b[4].strip()])
            full_text += page_text + "\n\n"
        doc.close()
        doc = None
        fitz.TOOLS.store_shrink(100)
        full_text = clean_document_text(full_text)
        if len(full_text.strip()) < 200:
            print("⚠️文本过少，启动OCR识别")
            full_text = ocr_pdf_stream(file_bytes)
        return full_text
    finally:
        if doc is not None:
            doc.close()


def extract_docx_bytes_text(file_bytes: bytes) -> str:
    from docx import Document
    with io.BytesIO(file_bytes) as stream:
        doc = Document(stream)
        text_list = []
        for p in doc.paragraphs:
            if p.text.strip():
                text_list.append(p.text.strip())
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        text_list.append(cell.text.strip())
    return clean_document_text("\n".join(text_list))


def extract_pptx_bytes_text(file_bytes: bytes) -> str:
    from pptx import Presentation
    with io.BytesIO(file_bytes) as stream:
        prs = Presentation(stream)
        text_list = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    text_list.append(shape.text.strip())
    return clean_document_text("\n\n".join(text_list))


def extract_old_doc_bytes_text(file_bytes: bytes) -> str:
    try:
        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        result = subprocess.run(["antiword", tmp_path], capture_output=True, text=True, timeout=30)
        os.unlink(tmp_path)
        return clean_document_text(result.stdout)
    except Exception:
        return ""


def extract_file_text(filename: str, file_bytes: bytes):
    """按扩展名提取文本，返回 (是否支持, 文本)"""
    suffix = filename.lower()
    if suffix.endswith(".pdf"):
        return True, extract_pdf_bytes_text(file_bytes)
    if suffix.endswith(".docx"):
        return True, extract_docx_bytes_text(file_bytes)
    if suffix.endswith(".pptx"):
        return True, extract_pptx_bytes_text(file_bytes)
    if suffix.endswith(".doc"):
        return True, extract_old_doc_bytes_text(file_bytes)
    return False, ""
