"""발표 슬라이드 HTML 생성: analysis.json + 2주차 실적 예시를 템플릿에 넣는다.

python code/final/build_slides.py --analysis data/final/analysis.json \
  --earnings data/week2/presentation_data.json --template code/final/slides_template.html \
  --repo <저장소 주소> --out slides/기말발표_1차.html
"""
import argparse, json
from pathlib import Path

NAMES = {"005930": "삼성전자", "000660": "SK하이닉스", "105560": "KB금융", "005380": "현대차", "055550": "신한지주"}

p = argparse.ArgumentParser()
for k in ("--analysis", "--earnings", "--template", "--out"):
    p.add_argument(k, type=Path, required=True)
p.add_argument("--repo", required=True)
a = p.parse_args()

earn = json.loads(a.earnings.read_text(encoding="utf-8"))["market"]["earnings"]["examples"]
data = dict(a=json.loads(a.analysis.read_text(encoding="utf-8")),
            earn=[dict(e, name=NAMES.get(e["code"], e["code"])) for e in earn])
html = (a.template.read_text(encoding="utf-8")
        .replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
        .replace("__REPO__", a.repo))
a.out.parent.mkdir(parents=True, exist_ok=True)
a.out.write_text(html, encoding="utf-8")
print("wrote", a.out, len(html), "bytes")
