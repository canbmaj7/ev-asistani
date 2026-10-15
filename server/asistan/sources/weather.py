"""Hava durumu: Open-Meteo."""
import httpx

from .. import config

# WMO hava kodu → (emoji, açıklama)
WMO = {
    0: ("☀️", "Açık"), 1: ("🌤", "Çoğunlukla açık"), 2: ("⛅", "Parçalı bulutlu"), 3: ("☁️", "Kapalı"),
    45: ("🌫", "Sisli"), 48: ("🌫", "Kırağılı sis"),
    51: ("🌦", "Hafif çisenti"), 53: ("🌦", "Çisenti"), 55: ("🌧", "Yoğun çisenti"),
    61: ("🌦", "Hafif yağmur"), 63: ("🌧", "Yağmur"), 65: ("🌧", "Kuvvetli yağmur"),
    66: ("🌧", "Dondurucu yağmur"), 67: ("🌧", "Kuvvetli dondurucu yağmur"),
    71: ("🌨", "Hafif kar"), 73: ("🌨", "Kar"), 75: ("❄️", "Yoğun kar"), 77: ("🌨", "Kar taneleri"),
    80: ("🌦", "Hafif sağanak"), 81: ("🌧", "Sağanak"), 82: ("⛈", "Şiddetli sağanak"),
    85: ("🌨", "Kar sağanağı"), 86: ("❄️", "Yoğun kar sağanağı"),
    95: ("⛈", "Gök gürültülü fırtına"), 96: ("⛈", "Dolu ile fırtına"), 99: ("⛈", "Şiddetli dolu ile fırtına"),
}


async def section(c: httpx.AsyncClient) -> str:
    r = await c.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": config.LAT, "longitude": config.LON, "timezone": str(config.TZ), "forecast_days": 1,
        "current": "temperature_2m,apparent_temperature,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "wind_speed_10m_max,sunrise,sunset",
    })
    r.raise_for_status()
    d = r.json()
    cur, day = d["current"], {k: v[0] for k, v in d["daily"].items()}
    emoji, text = WMO.get(day["weather_code"], ("🌡", "Bilinmiyor"))
    lines = [
        f"{emoji} Hava · {config.CITY}: {text}, şimdi {round(cur['temperature_2m'])}°"
        f" · {round(day['temperature_2m_min'])}°–{round(day['temperature_2m_max'])}°"
        f" · yağış %{day['precipitation_probability_max']}",
    ]
    if (day["precipitation_probability_max"] or 0) >= 50:
        lines.append("☔ Şemsiyeni al.")
    if day["temperature_2m_min"] <= 2:
        lines.append("🧣 Don/buzlanma olabilir, sıkı giyin.")
    return "\n".join(lines)


AQI_LEVELS = [(20, "İyi 🟢"), (40, "Makul 🟢"), (60, "Orta 🟡"), (80, "Kötü 🟠"), (100, "Çok kötü 🔴"), (10**9, "Son derece kötü 🟣")]


async def air_quality(c: httpx.AsyncClient) -> str:
    """Avrupa hava kalitesi endeksi (Open-Meteo)."""
    r = await c.get("https://air-quality-api.open-meteo.com/v1/air-quality", params={
        "latitude": config.LAT, "longitude": config.LON, "timezone": str(config.TZ),
        "current": "european_aqi,pm2_5,pm10"})
    r.raise_for_status()
    cur = r.json()["current"]
    aqi = cur.get("european_aqi")
    if aqi is None:
        raise ValueError("hava kalitesi verisi yok")
    level = next(label for limit, label in AQI_LEVELS if aqi <= limit)
    return f"🌫 Hava kalitesi: {level} (AQI {round(aqi)} · PM2.5 {round(cur.get('pm2_5') or 0)} · PM10 {round(cur.get('pm10') or 0)} µg/m³)"


async def forecast(c: httpx.AsyncClient) -> dict:
    """Anlık uyarılar için kısa vadeli tahmin: 15 dakikalık yağış (2 sa) ve saatlik (12 sa)."""
    r = await c.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": config.LAT, "longitude": config.LON, "timezone": str(config.TZ),
        "minutely_15": "precipitation", "forecast_minutely_15": 8,
        "hourly": "precipitation,wind_gusts_10m,weather_code,temperature_2m", "forecast_hours": 14})
    r.raise_for_status()
    return r.json()
