import base64
import os
from dotenv import load_dotenv
import httpx
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


class CoverAnalysis(BaseModel):
    is_cover_infographic: bool = Field(
        description="Является ли изображение обложкой/инфографикой с маркетинговыми бейджами, а не просто чистым фото прибора"
    )
    has_european_quality: bool = Field(
        description="Есть ли текст 'Европейское качество', 'Сделано в Европе' или символика ЕС"
    )
    country_in_text: str | None = Field(
        default=None,
        description="Страна из фразы 'Сделано в ...' (на русском: 'Германия', 'Италия', 'Польша')",
    )
    flag_country: str | None = Field(
        default=None,
        description="Страна, флаг которой изображен на картинке (на русском)",
    )
    flag_matches_text: bool = Field(
        default=False,
        description="Совпадает ли нарисованный флаг с указанной в тексте страной",
    )


def extract_page_data(html: str) -> tuple[str | None, str | None]:
    soup = BeautifulSoup(html, "html.parser")

    cover_url = None
    img_candidate = soup.select_one(
        ".product-item-detail-slider-image img, .detail-slider img, .product-slider img, .card-slider img"
    )

    if not img_candidate:
        gallery_link = soup.select_one('a[data-fancybox="gallery"], a.gallery-item')
        if gallery_link and gallery_link.get("href"):
            cover_url = gallery_link["href"]
        else:
            for img in soup.select(".product-detail img, .product-card img, img"):
                src = img.get("src", "")
                if "/upload/" in src and "logo" not in src.lower():
                    cover_url = src
                    break
    else:
        cover_url = img_candidate.get("src") or img_candidate.get("data-src")

    if cover_url and cover_url.startswith("/"):
        cover_url = "https://aeg-com.ru" + cover_url

    expected_country = None
    for row in soup.select("tr, li, div.char-item, .product-item-detail-properties tr"):
        text = row.get_text(" ", strip=True).lower()
        if any(k in text for k in ["страна", "производитель", "производства"]):
            val_el = row.select_one("td:last-child, span.char-value, dd")
            if val_el:
                expected_country = val_el.get_text(strip=True)
                break
            parts = [p.strip() for p in text.split(":") if p.strip()]
            if len(parts) > 1:
                expected_country = parts[1].title()
                break

    return cover_url, expected_country


def analyze_cover(image_url: str) -> CoverAnalysis:
    prompt = """
    Внимательно рассмотри изображение:
    1. Это обложка с графикой/надписями/плашками (инфографика) или просто фото товара без надписей?
    2. Есть ли надпись 'Европейское качество' (или аналогичная про качество/производство в Европе без указания конкретной страны)?
    3. Есть ли плашка/текст 'Сделано в [Страна]'? Какая страна названа?
    4. Присутствует ли графический флаг страны? Какой именно?
    5. Совпадает ли нарисованный флаг со страной в надписи?
    """

    resp = httpx.get(image_url, headers=HEADERS)
    b64_img = base64.b64encode(resp.content).decode("utf-8")
    media_type = resp.headers.get("content-type", "image/jpeg")

    response = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{b64_img}"},
                    },
                ],
            }
        ],
        response_format=CoverAnalysis,
    )
    return response.choices[0].message.parsed


def verify_product(url: str):
    print(f"\n--- Проверка: {url} ---")
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[FAIL] Не удалось загрузить страницу: {e}")
        return

    cover_url, expected_country = extract_page_data(resp.text)
    print(f"Обнаруженная обложка: {cover_url}")
    print(f"Страна в характеристиках: {expected_country}")

    if not cover_url:
        print("[FAIL] Изображение обложки не найдено.")
        return

    analysis = analyze_cover(cover_url)

    if not analysis.is_cover_infographic:
        print("[FAIL] Первое изображение — обычное фото товара, обложка отсутствует.")
        return

    if analysis.has_european_quality:
        print("[SUCCESS] Указано 'Европейское качество'. Страну сравнивать не нужно.")
        return

    if analysis.country_in_text:
        print(f"Текст на обложке: Сделано в {analysis.country_in_text}")
        print(f"Флаг на обложке: {analysis.flag_country}")

        if not analysis.flag_matches_text:
            print(f"[FAIL] Флаг ({analysis.flag_country}) не совпадает с текстом ({analysis.country_in_text}).")
            return

        if expected_country:
            exp = expected_country.lower().rstrip("яа")
            got = analysis.country_in_text.lower().rstrip("яа")
            if exp not in got and got not in exp:
                print(f"[FAIL] Страна на обложке ({analysis.country_in_text}) не совпадает с карточкой ({expected_country}).")
                return

        print("[SUCCESS] Обложка, флаг и страна из характеристик товара совпали.")
        return

    print("[FAIL] На обложке нет ни европейского качества, ни страны с флагом.")


if __name__ == "__main__":
    urls = [
        "https://aeg-com.ru/catalog/dukhovye-shkafy-aeg/dukhovoy-shkaf-aeg-tp9sb831at.html",
    ]
    for url in urls:
        verify_product(url)
