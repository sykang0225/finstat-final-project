# 금융통계 기말 프로젝트: 한국 주식시장 2년 반 (2024.04 ~ 2026.09)

교수 아카이브에 저장된 네이버 금융 종목 페이지 원본(HTML 2,403,074개, 날짜 폴더 591개)을 직접 읽어 만든 일별 종목 자료로,
코스피·코스닥 시장이 2년 반 동안 어떻게 바뀌었는지 공통 질문 4개에 답한다. 업종은 WICS 10개 대분류로 묶었다.

- 깃헙 페이지: https://sykang0225.github.io/finstat-final-project/
- 발표 자료: [`slides/기말발표_1차.html`](slides/기말발표_1차.html) (브라우저로 열기, → 키로 넘김, N 키로 발표 대본)
- 분석 결과 원본: [`data/final/analysis.json`](data/final/analysis.json)

## 주요 결과

| 질문 | 결과 |
|---|---|
| 1. 시가총액 | 상장사 시총 2,572조 → 5,848조 원(+127%). 증가분의 73%가 IT, 삼성전자·SK하이닉스 두 종목이 61.5%. IT 비중 43% → 60% (2025년 5월 31%까지 낮아졌다가 2026년 6월 67%) |
| 1. 상위 30 교체 | 진입: AI 인프라(SK스퀘어·삼성전기·전력기기 3사), 안보(한화에어로스페이스·HD현대중공업·두산에너빌리티·고려아연), 우리금융지주. 이탈 10개 중 6개만 시총 감소, 4개는 시장보다 덜 올라 밀려남 |
| 2. 업종 수익률 | 시총 가중 누적: IT +180.9%, 산업재 +144.4% … 소재 −10.6%. 업종 간 퍼짐 가운데값 12.7 → 39.0%p. 동일 가중으로 바꾸면 IT +19.6%(5위), 1위는 산업재 |
| 3. 업종 동조 | 업종 간 평균 상관: 급변일 0.82 vs 보통일 0.36. 급변일 53일 중 48일이 2026년 |
| 4. 거래량 | 급증 전 5일 +1.8%, 급증 후 20일 −0.8% (가운데값). 급증 종목 최다일은 2026-03-04 폭락일(1,113개) |

## 폴더 구조

```
slides/            발표 HTML, 사용한 디자인 토큰(getdesign.kr 토스 DESIGN.md)
code/extract/      원본 HTML → 일별 종목 표 추출
code/week1/        1주차: 코스피 급락 사건 선정·업종 회복 분석
code/week2/        2주차: 날짜 품질 검사, 시총 순위·거래량 규칙, 업종 요약 함수
code/final/        기말: WICS 매핑(wics.py), 질문 1~4 분석(analysis.py), 슬라이드 생성(build_slides.py)
data/enriched_daily/  거래일별 종목 표 550개 (원본 HTML에서 추출, 업종·종가·전일종가·거래량·시총)
data/week1/        코스피 종가 기준값, 1주차 결과
data/week2/        날짜 품질표(tables/sessions.csv), 2주차 결과, 실적 예시
data/server_summary/  추출 로그, 아카이브 목록, 실적 표 변경(추정→확정) 전환 목록
data/final/        기말 분석 결과(analysis.json), WICS 업종 일별 수익률(wics_sector_daily.zip)
```

원본 HTML 아카이브(수백 GB)와 서버 가공 묶음은 용량 때문에 올리지 않았다. `data/enriched_daily`부터는 이 저장소만으로 재현된다.

## 재현 방법

Python 3.10 이상, 표준 라이브러리만 사용한다.

```bash
# 1) 질문 1~4 분석 → data/final/analysis.json, data/final/wics_sector_daily.zip
python code/final/analysis.py --daily data/enriched_daily \
  --sessions data/week2/tables/sessions.csv --kospi data/week1/kospi_reference.csv \
  --start 2024-04-15 --end 2026-09-02 --out data/final/analysis.json

# 2) 발표 HTML 생성
python code/final/build_slides.py --analysis data/final/analysis.json \
  --earnings data/week2/presentation_data.json --template code/final/slides_template.html \
  --repo https://github.com/sykang0225/finstat-final-project --out slides/기말발표_1차.html
```

## 분석 규칙 요약

- **대상**: 코스피·코스닥 종목 중 업종이 있는 상장사. 업종이 없는 행(ETF·ETN 등)과 값이 충돌한 행은 제외.
- **날짜**: `sessions.csv`에서 품질 검사를 통과한 날만 사용(자료 누락·부족일 제외). 수익률은 직전 거래일과 이어지는 날만 계산.
- **업종**: 네이버 업종명 → WICS 10개 대분류(`code/final/wics.py`). 2차전지(전기제품)는 IT, 전력기기(전기장비)는 산업재, 부동산은 금융.
- **업종 수익률**: 종목 하루 수익률을 전일 시총으로 가중평균. 같은 업종·시장 유지, 전일 종가 연결, |수익률| ≤ 35%, 업종당 5종목 이상, 시총 확인 80% 이상인 날만. 비교용으로 동일 가중도 계산.
- **격차**: 월말 10개 업종 누적 수익률의 표준편차, 월말 비교일을 반으로 나눠 가운데값 비교(사분위 범위로 확인).
- **동조**: 코스피 하루 등락 절댓값 상위 10%를 급변일로 보고 업종 45쌍 상관 평균 비교(상위 20%로 확인).
- **거래량 급증**: 그날 거래량 ≥ 직전 20거래일 거래량 가운데값 × 2 (엄격 기준 × 3). 급증 전 5일, 후 5·10·20일 수익률 가운데값.
- 수익률은 종가 기준 산술 수익률이며 배당은 포함하지 않는다.

## 뉴스 출처

급변일과 상위 30 진입·이탈 종목의 원인은 같은 시기 국내 언론 보도로 확인했다. 기사 링크는 발표 자료 부록 C·D에 있다.
