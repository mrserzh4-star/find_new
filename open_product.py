"""Запускалка для власника: один клік — і продукт відкритий.

Навмисно НЕ припускає, що сервер уже працює, і НЕ вірить самому факту,
що порт зайнятий. 20.08 порт 8765 виявився захоплений стороннім
`python -m http.server`, який віддавав лістинг теки: сторінка «відкривалась»
і не була нашою. Тому перевірка — не «чи слухає порт», а «чи це МИ».
"""
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
URL = "http://localhost:8765"
PING = "http://127.0.0.1:8765/api/ping"
FILE_PAGE = HERE / "digest.html"


def ours() -> bool:
    """True лише якщо на порту відповідає САМЕ наш сервер."""
    try:
        import httpx

        r = httpx.get(PING, timeout=3)
        return r.status_code == 200 and r.json().get("server") == "find_new"
    except Exception:
        return False


def port_taken() -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", 8765)) == 0


def main() -> int:
    if ours():
        print("Сервер уже працює.")
        webbrowser.open(URL)
        return 0

    if port_taken():
        # Порт зайнятий, але відповідає не наш сервер. Мовчки відкрити
        # сторінку означало б показати власнику чуже — саме це й сталось.
        print("!! Порт 8765 зайнятий СТОРОННЬОЮ програмою.")
        print("   Те, що на ньому відкривається, — не наш продукт.")
        print(f"   Відкриваю сторінку з диска: {FILE_PAGE}")
        webbrowser.open(FILE_PAGE.as_uri())
        input("\n   Enter — закрити...")
        return 1

    print("Запускаю сервер...")
    subprocess.Popen(
        ["cmd", "/c", "start", "find_new server", "/min", "cmd", "/c", str(HERE / "serve.bat")],
        cwd=str(HERE),
        shell=False,
    )

    for _ in range(25):
        time.sleep(1)
        if ours():
            print("Готово. Відкриваю.")
            webbrowser.open(URL)
            return 0

    # Сервер не піднявся. Файлова сторінка гірша (немає кнопки збору),
    # але вона робоча — це чесніший вихід, ніж порожнє вікно браузера.
    print("!! Сервер не піднявся за 25 секунд.")
    if FILE_PAGE.exists():
        print(f"   Відкриваю сторінку з диска: {FILE_PAGE}")
        webbrowser.open(FILE_PAGE.as_uri())
    else:
        print("   І файлу digest.html теж немає — запусти run.bat.")
    input("\n   Enter — закрити...")
    return 1


if __name__ == "__main__":
    sys.exit(main())
