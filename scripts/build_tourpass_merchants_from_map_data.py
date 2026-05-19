#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build the Chungnam Tour Pass merchant CSV from the manually confirmed
city/category mapping plus clearly readable entries from the attached map.

Run:
    python scripts/build_tourpass_merchants_from_map_data.py
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_CSV = ROOT / "output" / "chungnam_tourpass_merchants.csv"
SOURCE_NOTE = "user_text_2026-05-19;attached_tourpass_map"
DATA_TEXT = "\n서산시\n관광\t서산 버드랜드\n체험\t서산 버드랜드 카페체험, 천수만생태관광, 올리공방, 마크라메 스튜디오 미래의감성, 여미갤러리, 더프라미스, 서산모사 뜨개방, 꽃은공방, 감태당, 종이그림, 여미도예, 사토와이예, 오딸기힐링팜\n카페\t서산 버드랜드 카페체험, 휴암미술관카페, 여미갤러리, 여기61, 해쉬브라운\n로컬\t정호두, 그린린, 로컬안내소 고로컬\n당진시\n관광\t삽교호 함상공원, 삽교호 해양테마체험관, 순성미술관, 아미미술관, 면천읍성안 그 미술관\n체험\t삽교호 자전거 터미널, 비움무인 드로잉스튜디오, 당진대일해운관광, 앵무세상, 퐁퐁벌룬\n카페\t카페보니또 삽교천점, 합덕백쌀카페, 아트바젤, 카페피어라\n로컬\t그레이스디포, 왜목제일횟집, 순성브루어리, 왕매실마을\n태안군\n관광\t아리랜드, 선샤인랜드, 태안국제원예치유박람회, 천리포수목원, 바람아래관광농원, 팜카밀레, 별똥별 하늘공원, 오마이갤러리, 안면도 쥬라기 박물관\n체험\t안면도 쥬라기 박물관 체험, 안면도 쥬라기 박물관 미디어영상관, 이로운 공방\n카페\t카페 꽃이머무는자리, 카페 미스터브리즈, 위로책방, 이호갤러리, 카페 신몽, 원산도커피\n로컬\t안면도 쥬라기 박물관 뮤지엄샵, 이플카페\n숙박\t알프스글램핑\n홍성군\n체험\t홍성도자문화 연구소, 쿠킹티어리, 홍성제과제빵학원, 테라코타이야기공방, 공실, 싸우라비검도, 장안스포츠센터, 도자기공방나무별, 플링바이올렛, 나리므의상실\n카페\t아미루트, 허그스, 제이비스트로, 티박스, 메이트커피마켓, 마서면이좋다\n로컬\t퍼니아트, 이미, 내포여여, 조양미술관협동조합, 프롬아인\n예산군\n관광\t내포보부상촌, 제이드플라워 갤러리, 아그로랜드 태신목장, 국립농업기상과학관\n체험\t이안아트팜, 일원공방, 오색꽃차치유농원, 뭉치다락방, 벚꽃로 18, 옹기발효음식전시체험관\n카페\t이안아트팜 카페, 카페라비아, 커피박\n로컬\t예산당, 삽교 곱창거리 곱창인가, 예산사과빵상회, 해가준예산황토사과, 온주자디저트랩\n아산시\n관광\t아산퍼스트빌리지공룡월드, 도고 아트홀\n체험\t아산레일바이크, 오월랑공방, 공방단디, 푸루상점, 조은아한복, 꼼지락이야기, 고요\n카페\t카페백묘국 청수청당점, 카페호두, 카페백묘국 신부점, 미인상회, 작은커피나무, 빈스\n로컬\t복담다\n숙박\t라마다앙코르 바이 원덤/윈덤\n천안시\n관광\t아라리오갤러리, 아름다운정원 화수목, 1923역사관, 천안상록리조트상록랜드, 엄마놀이터\n체험\t투데이이즈유어벌스데이, 이일리 아로마오감교육센터, 아침나공화국, 팀레드비 우리주짓수, 페어링 에피소드, Lets Craft 노드랩, 늘솜아트, 구시\n카페\t랜드마크 195, 카페 찬바우, 캐슬1477\n로컬\t손수 만든 작은 상점, 제원다방\n숙박\tINK 관광호텔, 아우내쉼플스테이, 상록리조트\n공주시\n관광\t고운식물원\n체험\t세라믹 아트 공작소, 아가새농장, 도깨비노리터, 머리공방, 흙담은 도자기 공방, 온포인트핏, 호호공방, 마마캔즈, 동그랑작업실, 리멋, 옥이쓰공방\n카페\tYes mountain, 삼화양조장, 샐러드담다, 카페단소\n로컬\t학계애화덕피자, 판교 책방, 눈맞추다, 기범이네 국수\n숙박\t한옥1954\n부여군\n관광\t백제문화단지+백제문화역사관, 부소산성, 부여동물원, 백제문화체험박물관, 어린이 백제 체험관\n체험\t홍산장시사람들 체험, 별라솜\n카페\t수북로 1945, 가림상회, 카페장은리\n로컬\t홍산장시사람들 솜솜이빵, 팜젤라또\n숙박\t백제호텔\n보령시\n관광\t무창포타워, 보령석탄박물관, 성주산자연휴양림, 바둑이네 동물원\n체험\t보령머드뷰티치유관, 대천브루어리, 우유창고, 스킨포레스트, 더 마리나, 더별꽃, 조이너리, 꿈꾸는달님도자기, 수련족욕카페, 카페에덴바, 몽선부엉이체험마을\n카페\t숲속향기카페, 덴오브마운틴\n로컬\t보령머드화장품, 성주사지 천년역사관, 대천브루어리 수제맥주 양조장&피자펍, 대천아빠다 with 대천브루어리, 마케 with 대천브루어리, 서담상회\n서천군\n관광\t국립생태원, 이용노의 집\n체험\t서천물버들체험휴양마을, 어린이 감성체험장\n카페\t카페로우\n청양군\n관광\t칠갑산천문대스타파크, 목재문화자연사체험관\n체험\t목재문화체험장, 서작가, 리리플라워\n로컬\t온기옥보쌈, 빈관, 칠갑산 청정 한우타운, 청양고추빵공장, 에이티엠스튜디오, 포포스토리, 청양고덕갈비, 새이학가든\n논산시\n체험\t오감그리다논산점, 지산농원, 종이문화재단 논산교육원, 세라인스튜디오, 양촌와이너리, 드론앤톡톡\n카페\t투썸플레이스 벌곡휴게소점, 심스커피, 카페워니, 어드레스&붐도넛, 루치아의 뜰, 내재\n로컬\t노성산성in가배, 탑정호 in가배, 탑정호 WACU, 금성다방, 가이옥 돌솥설렁탕, 금복도45, 따뜻한 밥상\n금산군\n로컬\t갈비에 반하다, 상신식당\n"
IMAGE_ONLY_ROWS = [
  [
    "계룡시",
    "체험",
    "테라 도자기공방",
    0.62,
    True
  ],
  [
    "계룡시",
    "카페",
    "카페1896",
    0.62,
    True
  ]
]
CATEGORY_TO_CSV = {
  "관광": "관광지",
  "체험": "체험",
  "카페": "카페",
  "로컬": "로컬시설",
  "숙박": "숙박"
}


def parse_rows() -> list[tuple[str, str, str, float, bool]]:
    rows: list[tuple[str, str, str, float, bool]] = []
    city = ""
    for raw in DATA_TEXT.strip().splitlines():
        line = raw.strip()
        if not line:
            continue
        if "\t" not in line:
            city = line
            continue
        category, names_raw = line.split("\t", 1)
        csv_category = CATEGORY_TO_CSV[category.strip()]
        for name in [n.strip() for n in names_raw.split(",") if n.strip()]:
            rows.append((city, csv_category, name, 0.78, False))
    rows.extend(
        (city, CATEGORY_TO_CSV[category], name, confidence, needs_review)
        for city, category, name, confidence, needs_review in IMAGE_ONLY_ROWS
    )
    return rows


def main() -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "city",
                "merchant_name",
                "category",
                "benefit_type",
                "address",
                "phone",
                "source_url",
                "source_image",
                "raw_text",
                "confidence",
                "needs_review",
            ],
        )
        writer.writeheader()
        seen: set[tuple[str, str, str]] = set()
        for city, category, name, confidence, needs_review in parse_rows():
            key = (city, category, name)
            if key in seen:
                continue
            seen.add(key)
            writer.writerow(
                {
                    "city": city,
                    "merchant_name": name,
                    "category": category,
                    "benefit_type": "확인필요",
                    "address": "",
                    "phone": "",
                    "source_url": "",
                    "source_image": SOURCE_NOTE,
                    "raw_text": f"{city}|{category}|{name}",
                    "confidence": f"{confidence:.2f}",
                    "needs_review": str(bool(needs_review)).lower(),
                }
            )
    print(f"Wrote {OUT_CSV} ({len(parse_rows())} source rows)")


if __name__ == "__main__":
    main()
