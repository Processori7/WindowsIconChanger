# WinIcons

**Автор:** ProcessorI7 · **Сайт:** https://github.com/Processori7/ · Первая версия программы (1.0)

Программа для Windows: накладывает **вашу картинку поверх кнопки «Пуск»** на панели задач.

- Значок рисуется прозрачным окном-оверлеем *поверх* панели задач: клики проходят сквозь него, кнопка «Пуск» работает как обычно.
- **Реестр не изменяется.**
- Значок виден поверх панели задач и обычных окон и сам возвращается после взаимодействий с оболочкой (меню «Пуск», поиск) — см. «Известное ограничение».
- Живёт в трее: **ЛКМ** — настройки, **ПКМ** — меню `Настройки…` / `Выход`. Состояние (найдена ли кнопка «Пуск») видно в подсказке значка трея.

## Известное ограничение

Пока открыто меню «Пуск», поиск или вы активно взаимодействуете с панелью задач, Windows 11 рисует состояние кнопки «Пуск» в защищённом системном слое поверх всех окон — в эти моменты значок временно скрывается. Как только меню закрывается или фокус переходит на окно обычной программы, значок появляется снова сам, без каких-либо действий.

Это ограничение самой Windows: рисовать поверх защищённого слоя оболочки может только подписанная UIAccess-программа (требует сертификата и установки в Program Files); все доступные способы для обычного приложения проверены — система их отклоняет.

## Настройки

- **Галерея (верхняя полоса)** — все выбранные картинки сохраняются плитками: клик по плитке возвращает этот значок, список переживает перезапуск, листается колесом мыши. Лимита нет — хранятся все картинки, которые вы выбирали.
- **Выбрать изображение…** — любая картинка или фото (PNG/JPG/WEBP/BMP/GIF/SVG). Файл используется **прямо из своей папки, без копирования** — если файл переместить или удалить, плитка исчезнет из галереи.
- **Пресеты** — готовые значки из папки `assets\icons` (сейчас там Star Wars).
- **Форма** — скруглённый квадрат / круг / квадрат (удобно для фотографий).
- **Размер** — «Авто» подбирается под высоту кнопки «Пуск», либо вручную.
- **Положение** — стрелки для точного сдвига или «Калибровка»: перетащите значок мышью прямо на панели задач.
- Автозагрузка и скрытие в полноэкранных приложениях.
- **Журнал** — галочка «Вести журнал работы» (по умолчанию **выключена**): пишет события программы в `%APPDATA%\IconsPo\iconspo.log` — пригодится для диагностики.
- **Тема и язык** — кнопки «Тема: Тёмная/Светлая» и «Язык: Русский/English»: переключают оформление окна и переводят весь интерфейс (настройки, меню трея, подсказки). Выбор сохраняется в конфиге.

## Автозагрузка

Галка «Запускать при входе в Windows» включает один из двух способов (реестр не трогается):

1. **Задача в Планировщике задач** — надёжнее: её не отключают «чистки» автозагрузки, запускается от вашего имени в вашей сессии. Для создания нужны права администратора — появится **запрос UAC**.
2. **Ярлык в папке «Автозагрузка»** — запасной способ без прав администратора (если запрос UAC отклонить или политика запрещает создание задач).

Почему не служба Windows (`sc create`)? Службы работают в отдельной сессии 0 и не имеют доступа к рабочему столу — оверлей поверх панели задач показать невозможно. Планировщик задач даёт ту же надёжность, но запускает программу в вашей пользовательской сессии.

## Ресурсы

Приложение почти не потребляет ресурсов: обновление — раз в 1,5 секунды, картинка перерисовывается только при изменениях, опрос кнопки «Пуск» — один быстрый запрос к панели задач.
Замер в простое (Windows 11, 2560×1600): **≈124 МБ памяти у основного процесса (плюс ≈35 МБ у служебного загрузчика onefile) и всего ≈31 мс процессорного времени за 40 секунд (≈0,08 % одного ядра)**.

## Запуск из исходников

```powershell
cd icons_po
python -m venv venv                            # только если виртуального окружения ещё нет
venv\Scripts\pip install -r requirements.txt   # PySide6 + uiautomation
venv\Scripts\python main.py
```

При первом запуске откроется окно настроек, дальше программа живёт в трее.
В Windows 11 новый значок трея по умолчанию попадает в скрытую область «^» — при желании перетащите его на видимую часть панели.

## Свои пресеты

Любой `.svg` из папки `assets\icons` автоматически становится кнопкой-пресетом в настройках
(название берётся из `PRESET_TITLES` в `main.py`, иначе — из имени файла).
Чтобы добавить пресет: положите файл в `assets\icons` и пересоберите exe.
Для разового значка проще «Выбрать изображение…» — подойдёт файл из любой папки.

## Сборка одного exe (Nuitka)

```powershell
venv\Scripts\pip install -r requirements-build.txt   # nuitka + zstandard + pillow (сверх requirements.txt)
venv\Scripts\python build_icon.py
venv\Scripts\python -m nuitka --onefile --enable-plugin=pyside6 ^
  --windows-console-mode=disable --windows-icon-from-ico=assets\icon.ico ^
  --include-data-dir=assets=assets ^
  --include-package=comtypes --include-package=uiautomation --include-package-data=uiautomation ^
  --company-name=ProcessorI7 ^
  --copyright="(c) ProcessorI7 - https://github.com/Processori7/" ^
  --file-description="WinIcons 1.0 - a custom picture over the Start button (no registry changes)" ^
  --nofollow-import-to=PIL ^
  --output-dir=dist --output-filename=WinIcons.exe ^
  --product-name=WinIcons --file-version=1.0.0.0 --product-version=1.0.0.0 ^
  --assume-yes-for-downloads --remove-output main.py
```

Результат: `dist\WinIcons.exe` — один файл, ничего дополнительно не требует.
Под картинку подкладывается цвет панели задач, поэтому сквозь прозрачные места значка не просвечивает настоящий логотип.

## Самопроверка

```powershell
venv\Scripts\python main.py --selftest     # или dist\WinIcons.exe --selftest
```

Создаёт в текущей папке `selftest_report.json` и скриншоты `selftest_before/after/settings.png`.

## Как это выглядит в системе

- Значок в трее — миниатюра вашей картинки.
- Пока игра/приложение в полноэкранном режиме, оверлей сам прячется.
- Если панель задач на другой высоте/DPI, положение можно поправить стрелками в настройках.

---

# WinIcons (English)

**Author:** ProcessorI7 · **Website:** https://github.com/Processori7/ · First release (1.0)

A Windows app that draws **your picture over the Start button** on the taskbar.

- The icon is a transparent click-through overlay *on top of* the taskbar — clicks pass through it, and the Start button keeps working as usual.
- **The registry is never modified.**
- The icon stays on top of the taskbar and normal windows and restores itself after shell interactions (Start menu, search) — see "Known limitation".
- Lives in the system tray: **left click** — settings, **right click** — menu (`Settings…` / `Exit`). The tray tooltip shows the current state (whether the Start button was found).

## Known limitation

While the Start menu, search or another shell flyout is open — or while you are actively interacting with the taskbar — Windows 11 draws the Start button state in a protected system layer above all windows, so the icon hides for that moment. As soon as the menu closes or focus moves to a normal app window, the icon comes back on its own, with nothing to press.

This is a Windows limitation: only a signed UIAccess program (which needs a certificate and installation into Program Files) can draw above that protected shell layer; every approach available to a regular app was tried and is rejected by the system.

## Settings

- **Gallery (top strip)** — every picture you pick is kept as a tile: click a tile to switch back to that icon, the list survives restarts and scrolls with the mouse wheel. There is no limit — every picture you have ever picked is kept.
- **Choose image…** — any picture or photo (PNG/JPG/WEBP/BMP/GIF/SVG). The file is used **in place, without copying** — if you move or delete the file, its tile disappears from the gallery.
- **Presets** — ready-made icons from `assets\icons` (Star Wars for now).
- **Shape** — rounded square / circle / square (handy for photos).
- **Size** — "Auto" matches the height of the Start button, or set it manually.
- **Position** — arrows for fine tuning, or "Calibration": drag the icon right on the taskbar.
- Autostart at Windows logon and hiding in fullscreen apps.
- **Log** — the "Keep a log" checkbox (off by default): writes app events to `%APPDATA%\IconsPo\iconspo.log` for diagnostics.
- **Theme and language** — the "Theme: Dark/Light" and "Language: Russian/English" buttons switch the window theme and translate the whole interface (settings, tray menu, tooltips). The choice is saved in the config.

## Autostart

The "Run at Windows logon" checkbox uses one of two ways (the registry is never touched):

1. **A Task Scheduler task** — more reliable: startup cleanup tools don't disable it, and it runs as you in your own session. Creating it needs administrator rights, so a **UAC prompt** appears.
2. **A shortcut in the Startup folder** — the fallback that needs no admin rights (used when you decline the UAC prompt or policy forbids creating tasks).

Why not a Windows service (`sc create`)? Services run in a separate session 0 with no access to the desktop — an overlay over the taskbar simply cannot be shown there. A scheduled task gives the same reliability but runs the app in your user session.

## Resource usage

The app barely uses any resources: it refreshes once every 1.5 seconds, the picture is re-rendered only when something changes, and the Start button lookup is a single fast query to the taskbar.
Idle measurement (Windows 11, 2560×1600): **≈124 MB of memory for the main process (plus ≈35 MB for the onefile launcher) and just ≈31 ms of CPU time over 40 seconds (≈0.08 % of one core)**.

## Running from source

```powershell
cd icons_po
python -m venv venv                                    # create the venv if you don't have one
venv\Scripts\pip install -r requirements.txt           # PySide6 + uiautomation
venv\Scripts\python main.py
```

The settings window opens on the first run; after that the app lives in the tray.
On Windows 11 a new tray icon goes into the hidden "˄" area by default — drag it out if you like.

## Custom presets

Any `.svg` in `assets\icons` automatically becomes a preset button in the settings
(the title comes from `PRESET_TITLES` in `main.py`, otherwise from the file name).
To add a preset: drop the file into `assets\icons` and rebuild the exe.
For a one-off icon just use "Choose image…" — a file from any folder works.

## Building a single exe (Nuitka)

```powershell
venv\Scripts\pip install -r requirements-build.txt   # nuitka + zstandard + pillow (on top of requirements.txt)
venv\Scripts\python build_icon.py
venv\Scripts\python -m nuitka --onefile --enable-plugin=pyside6 ^
  --windows-console-mode=disable --windows-icon-from-ico=assets\icon.ico ^
  --include-data-dir=assets=assets ^
  --include-package=comtypes --include-package=uiautomation --include-package-data=uiautomation ^
  --company-name=ProcessorI7 ^
  --copyright="(c) ProcessorI7 - https://github.com/Processori7/" ^
  --file-description="WinIcons 1.0 - a custom picture over the Start button (no registry changes)" ^
  --nofollow-import-to=PIL ^
  --output-dir=dist --output-filename=WinIcons.exe ^
  --product-name=WinIcons --file-version=1.0.0.0 --product-version=1.0.0.0 ^
  --assume-yes-for-downloads --remove-output main.py
```

The result is `dist\WinIcons.exe` — a single file with no extra dependencies.
The taskbar colour is placed under your picture, so the original logo does not show through transparent areas.

## Self-check

```powershell
venv\Scripts\python main.py --selftest     # or dist\WinIcons.exe --selftest
```

Writes `selftest_report.json` and the screenshots `selftest_before/after/settings.png` into the current folder.

## In the system

- The tray icon is a thumbnail of your picture.
- While a game or app is fullscreen, the overlay hides itself.
- If your taskbar has a different height/DPI, adjust the position with the arrows in the settings.
