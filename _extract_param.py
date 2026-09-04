# -*- coding: utf-8 -*-
import pymupdf

src = r"C:\Users\16324\Desktop\IDEF\参考文献\参数证明文献\无线网络衰落和损耗的建模与仿真研究_任智.pdf"
out = r"C:\Users\16324\Desktop\IDEF\_param_proof.txt"

doc = pymupdf.open(src)
print("pages:", doc.page_count)
with open(out, "w", encoding="utf-8") as f:
    for i, page in enumerate(doc):
        f.write(f"\n===== PAGE {i+1} =====\n")
        f.write(page.get_text())
print("done ->", out)
