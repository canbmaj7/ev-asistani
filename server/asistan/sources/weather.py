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
        f"{emoji} Hava · {config.CITY}",
        f"{text}, şimdi {round(cur['temperature_2m'])}° (hissedilen {round(cur['apparent_temperature'])}°)",
        f"En yüksek {round(day['temperature_2m_max'])}° / en düşük {round(day['temperature_2m_min'])}°"
        f" · yağış %{day['precipitation_probability_max']} · rüzgâr {round(day['wind_speed_10m_max'])} km/sa",
        f"🌅 {day['sunrise'][-5:]} · 🌇 {day['sunset'][-5:]}",
    ]
    if (day["precipitation_probability_max"] or 0) >= 50:
        lines.append("☔ Şemsiyeni al.")
    if day["temperature_2m_min"] <= 2:
        lines.append("🧣 Don/buzlanma olabilir, sıkı giyin.")
    return "\n".join(lines)
