# Резервные копии production

Задача 0.11. Решение и сроки хранения — decisions.md → «Бэкапы production».

## Два слоя

| Слой | Что делает | От чего спасает | От чего не спасает |
| --- | --- | --- | --- |
| **Railway Pro** (deploy.md → «7. Резервные копии Railway») | Снимки базы по расписанию и восстановление на любой момент примерно за 4 недели (PITR) | Ошибки: неудачная миграция, удалённые строки, испорченные данные | Удаление тома базы (копии удаляются вместе с ним), потеря или взлом аккаунта Railway |
| **Внешний бэкап** (этот документ) | Раз в сутки — зашифрованный `pg_dump` базы и копия бакета с файлами в Backblaze B2, регион EU Central (Амстердам) | Потеря проекта, тома или аккаунта Railway; взлом Railway: копии зашифрованы и защищены от удаления | Последние часы перед сбоем: копия делается раз в сутки |

Как устроен внешний бэкап:

- **Где работает.** Сервис `backup` в окружении `production` на Railway, запускается по расписанию. Код — `ops/backup/`:
  - `backup.sh` — сам бэкап;
  - `restore.sh` — восстановление;
  - `Dockerfile` — образ на основе `postgres` нужной версии с `age` и `rclone` (rclone 1.74 из его официального образа: в Debian версия старая и не умеет ставить блокировку).
- **Шифрование.** Копия базы шифруется [age](https://age-encryption.org) открытым ключом. Закрытый ключ есть только у владельца, в Railway его нет. Даже с доступом к Railway и к B2 копию не прочитать.
  - Файлы клиентов и так зашифрованы ключом визовика (`backend/app/core/documents.py`) и копируются как есть.
  - Незашифрованное на диск не пишется.
- **Защита от удаления.** Бакет в B2 создаётся с блокировкой удаления (Object Lock) в режиме Compliance: каждый новый файл 30 дней не может удалить никто, даже владелец аккаунта. Ежемесячная копия живёт год, поэтому при загрузке получает свою блокировку Compliance на 366 дней.
- **Ключи.** У сервиса `backup` два ключа B2, каждый только для своей папки. `railway-backup-db` пишет копии базы в `db/` и ставит им блокировку, `railway-backup-files` ведёт зеркало файлов в `files/`. Ни один из них не может удалить сохранённую версию файла, сократить блокировку или поменять настройки бакета. Тот, кто прочитает переменные сервиса в Railway, копии не уничтожит. Самое большее он скроет файлы, а скрытое B2 удаляет только через 30 дней (копии базы — не раньше, чем кончится их блокировка). За это время можно заметить и вернуть их (раздел 4).
- **Сроки хранения** задают правила жизненного цикла бакета:

  | Что | Папка | Срок | Блокировка |
  | --- | --- | --- | --- |
  | ежедневные копии | `db/daily/` | 30 дней | 30 дней, весь срок |
  | ежемесячные копии (за 1-е число) | `db/monthly/` | 12 месяцев | 366 дней, весь срок |
  | файлы, удалённые в CRM | `files/` | 30 дней после удаления | 30 дней после загрузки |

- **Проверка.** На каждом PR задание CI `backup` проходит весь путь на демо-данных: бэкап → проверка, что копия зашифрована → восстановление в отдельный Postgres без сети → сверка каждой таблицы по содержимому, плюс RLS, политики, права и триггеры. Ещё оно проверяет, что в восстановленной базе нет сессий входа, что `restore.sh` не берёт подброшенные в бакет копии и не принимает пароль базы в адресе. Тестовый S3 там устроен как бакет B2: с версиями, блокировкой и ключами с правами из раздела 2. Задание проверяет, что ежемесячная копия заблокирована на год и её не удаляет даже владелец, что ключи сервиса пишут только в свою папку и не удаляют версии, а файл, удалённый в CRM, остаётся в зеркале скрытой версией. Раз в месяц владелец проверяет настоящую копию (раздел 5).
- **Цена.** В B2 первые 10 ГБ бесплатно, дальше $6,95 за ТБ в месяц. Сервис cron работает минуту в сутки и укладывается в использование, которое входит в Pro.

## 1. Ключ шифрования

Делается один раз, на своём компьютере. Нужен запущенный Docker Desktop: значок кита в трее, статус «Engine running». Команды — из папки репозитория, ключ записывается в папку `visacrm-keys` в домашнем каталоге, вне репозитория:

```bash
docker build -t visacrm-backup ops/backup
mkdir ~/visacrm-keys
docker run --rm -v ~/visacrm-keys:/out --entrypoint age-keygen visacrm-backup -o /out/visacrm-backup-key.txt
```

В Windows PowerShell третья команда та же, только с `"$HOME\visacrm-keys:/out"` вместо `~/visacrm-keys:/out`. Файл пишет сама `age-keygen`: перенаправление `>` в PowerShell сохранило бы его в UTF-16, и `age` его не прочитает.

Файл `visacrm-backup-key.txt` — **закрытый ключ**. Без него ни одну копию не расшифровать. Команда печатает `Public key: age1…` — это открытый ключ, та же строка есть и в файле.

1. Сохраните файл в менеджере паролей и сделайте вторую копию офлайн (флешка в надёжном месте).
2. Удалите файл с диска.
3. В репозиторий, Railway и B2 файл не кладите никогда.

Строка `# public key: age1…` в этом файле — **открытый ключ**. Он пойдёт в переменную `BACKUP_AGE_RECIPIENT`.

Для восстановления нужен ещё `VAULT_MASTER_KEY` production. Его вторая копия уже хранится вне Railway (operations.md → «Мастер-ключ сейфа»). Без него в восстановленной базе не откроются пароли сейфа и файлы.

## 2. Backblaze B2

1. **Аккаунт.** При регистрации на backblaze.com выберите регион **EU Central**. Регион задаётся один раз, для всего аккаунта.
2. **Бакет.** B2 Cloud Storage → **Buckets** → **Create a Bucket**:
   - Files in Bucket — **Private**;
   - Default Encryption — Enable;
   - **Object Lock — Enable.** Включите сразу: выключить его потом нельзя;
   - имя, например `visacrm-backup-<что-то своё>`: имена бакетов общие для всех клиентов B2.
3. **Блокировка удаления.** В списке бакетов нажмите у бакета **Object Lock: Enabled**, укажите срок **30** дней и нажмите **Save changes**. Срок действует для файлов, загруженных после сохранения, поэтому задайте его до первого запуска бэкапа. B2 ставит такую блокировку в режиме Compliance: в сведениях о файле видно «Object Lock: Compliance Mode». Сократить или снять её не может никто, ни один ключ, ни сам владелец.
4. **Сроки хранения.** **Lifecycle Settings** → **Use custom lifecycle rules**, три правила. Каждое — со своим **File Path**, поле дней, которого нет в таблице, оставьте пустым. Сохраните кнопкой **Update Bucket**. Правило с пустым File Path действует на весь бакет — такого не делайте.

   | File Path | Days from uploading to hiding | Days from hiding to deleting |
   | --- | --- | --- |
   | `db/daily/` | 30 | 1 |
   | `db/monthly/` | 365 | 1 |
   | `files/` | — | 30 |

5. **Ключи для бэкапа.** Два ключа, каждый для своей папки и без права удалять. В веб-интерфейсе B2 такие не создать: там только готовые наборы прав, и Read and Write даёт ключу ещё удаление, обход блокировки Governance, ссылки на файлы и смену настроек бакета. Поэтому ключи создаются утилитой B2 CLI на своём компьютере.
   1. Установите B2 CLI версии 4.1 или новее: `pip install b2` (нужен Python 3.9+) или файл `b2-windows.exe` со страницы выпусков [B2_Command_Line_Tool](https://github.com/Backblaze/B2_Command_Line_Tool/releases).
   2. Войдите мастер-ключом аккаунта: `b2 account authorize`, программа спросит `keyID` и сам ключ. Мастер-ключ — в B2 → **Application Keys** → **Master Application Key**. Если его нет под рукой, сгенерируйте новый. Прежний мастер-ключ при этом перестанет работать, через S3 он и так не нужен.
   3. Создайте ключи, подставив имя бакета:

      ```bash
      b2 key create --bucket visacrm-backup-<…> --name-prefix db/ railway-backup-db listBuckets,listAllBucketNames,readBuckets,listFiles,readFiles,writeFiles,readFileRetentions,writeFileRetentions
      b2 key create --bucket visacrm-backup-<…> --name-prefix files/ railway-backup-files listBuckets,listAllBucketNames,readBuckets,listFiles,readFiles,writeFiles
      ```

      Каждая команда печатает две строки: `keyID` и сам ключ (`applicationKey`). Ключ показывается один раз: сразу сохраните обе строки в менеджере паролей.
   4. Проверьте: `b2 key list --long`. У каждого ключа должны быть ровно эти права и свой префикс (`db/` или `files/`).
   5. Выйдите: `b2 account clear`. Так мастер-ключ не останется на компьютере.

   Зачем каждое право:

   | Право | Зачем |
   | --- | --- |
   | `listBuckets`, `listAllBucketNames`, `readBuckets` | найти бакет через S3. Без `listAllBucketNames` ключ одного бакета получает ошибку авторизации на части запросов |
   | `listFiles`, `readFiles` | rclone перед загрузкой смотрит, нет ли уже такого файла, а зеркалу нужен список файлов. Копии зашифрованы, без закрытого ключа age их не прочитать |
   | `writeFiles` | загрузка. В `files/` ещё скрытие файлов, удалённых в CRM: скрытый файл B2 удалит через 30 дней по правилу из шага 4 |
   | `writeFileRetentions`, `readFileRetentions` | только у `railway-backup-db`: блокировка ежемесячной копии на 366 дней |

   Каких прав нет и почему: `deleteFiles` удаляет сохранённые версии, `bypassGovernance` снимает блокировку Governance, `shareFiles` даёт ссылки на скачивание, права `write…Bucket…` меняют блокировку по умолчанию, правила хранения и другие настройки бакета.

   Что остаётся. С `writeFileRetentions` ключ `railway-backup-db` может заблокировать надолго любой файл в `db/`: блокировку Compliance можно только продлить. Кто украдёт этот ключ, может забить бакет мусором, который нельзя будет удалить. От счёта за это — лимит из шага 7.
6. **Ключ для восстановления.** Третий ключ, `restore`, создаётся в веб-интерфейсе: тот же бакет, тип **Read Only**, галочка **Allow List All Bucket Names**, без префикса. Его храните рядом с закрытым ключом age, в Railway он не нужен. В `b2 key list --long` у него должны быть только права `list…` и `read…` (и, может быть, `shareFiles`): никаких `write…`, `deleteFiles` и `bypassGovernance`.
7. **Лимит расходов.** B2 → **Caps & Alerts**: поставьте дневной лимит на хранение (Storage Cap) с запасом выше нынешнего и включите письма о приближении к нему. Когда лимит достигнут, B2 перестаёт принимать файлы: запуск бэкапа становится красным, и это видно сразу.
8. **Адрес бакета.** На странице бакета указан **Endpoint**, например `s3.eu-central-003.backblazeb2.com`; его средняя часть — регион (`eu-central-003`).

## 3. Сервис `backup` в Railway

Только в окружении **production**. В `staging` сервис не создавайте: его копировал бы каждый стенд PR.

1. **Версия Postgres.** Postgres → **Data** → Query: `SHOW server_version;`. Если первая цифра не 18, позже понадобится переменная `PG_MAJOR` (шаг 4): `pg_dump` должен быть той же версии, что сервер, или новее.
2. **Создание сервиса.** **+ Create → GitHub Repo** → этот репозиторий. Назовите сервис `backup`.
3. **Settings:**
   - Source → **Root Directory**: `ops/backup`;
   - Build → **Dockerfile Path**: `/ops/backup/Dockerfile` — путь от корня репозитория. Без него Railway собирает своим сборщиком (Railpack), а не по `Dockerfile`. В логе сборки должно быть `load build definition from ops/backup/Dockerfile`;
   - Deploy → **Cron Schedule**: `0 1 * * *` — каждый день в 01:00 UTC (04:00 по Минску);
   - Deploy → Restart Policy: **Never**; Healthcheck Path — пусто;
   - Networking — домен не создавайте: сервису не нужен вход снаружи.
4. **Variables:**

   ```
   DATABASE_URL=${{Postgres.DATABASE_URL}}
   S3_ENDPOINT=${{Files.ENDPOINT}}
   S3_REGION=${{Files.REGION}}
   S3_BUCKET=${{Files.BUCKET}}
   S3_ACCESS_KEY=${{Files.ACCESS_KEY_ID}}
   S3_SECRET_KEY=${{Files.SECRET_ACCESS_KEY}}
   BACKUP_S3_ENDPOINT=https://s3.eu-central-003.backblazeb2.com
   BACKUP_S3_REGION=eu-central-003
   BACKUP_S3_BUCKET=<имя бакета>
   BACKUP_S3_DB_ACCESS_KEY=<keyID ключа railway-backup-db>
   BACKUP_S3_DB_SECRET_KEY=<applicationKey этого ключа>
   BACKUP_S3_FILES_ACCESS_KEY=<keyID ключа railway-backup-files>
   BACKUP_S3_FILES_SECRET_KEY=<applicationKey этого ключа>
   BACKUP_AGE_RECIPIENT=age1…
   ```

   - `Postgres` и `Files` — имена сервиса базы и бакета, как в deploy.md.
   - `BACKUP_S3_ACCESS_KEY` и `BACKUP_S3_SECRET_KEY` у сервиса быть не должно. Это переменные старого ключа с полными правами: с ними копия делается, но запуск заканчивается ошибкой (раздел 6).
   - Если бакета файлов ещё нет, вместо `S3_*` поставьте `BACKUP_FILES=off`, тогда и `BACKUP_S3_FILES_*` не нужны. Когда бакет появится, верните `S3_*`, добавьте `BACKUP_S3_FILES_*` и уберите `BACKUP_FILES`.
   - `PG_MAJOR=<версия>` — только если на шаге 1 вышла не 18.
   - `BACKUP_HEARTBEAT_URL` — по желанию: адрес сервиса-наблюдателя (например, healthchecks.io). Скрипт обращается к нему после каждой успешной копии, и наблюдатель пишет вам, если копии перестали приходить.
5. **Первый запуск.** **Deploy**. В логах должно быть `backup: db/daily/visacrm-….dump.age, N bytes` и `backup: files mirrored, …`, а 1-го числа ещё `backup: db/monthly/…, locked until <дата через 366 дней>`. В B2 в бакете появятся `db/daily/` и `files/`. Если запуск красный, причина — в последних строках лога: `… is not set`, ошибка `pg_dump` или `rclone`, `still holds BACKUP_S3_ACCESS_KEY` (раздел 6).

## 4. Восстановление

Сначала решите, какой слой нужен:

- **Ошибка в данных** (неудачная миграция, удалили лишнее), а проект Railway цел — восстановление Railway: Postgres → **Backups** → PITR на нужную минуту (deploy.md, раздел 7). Это быстрее и точнее суточной копии. Railway создаст рядом новый сервис Postgres с данными на эту минуту, старая база останется как была. Прежде чем поставить веб-сервису `DATABASE_URL` на новую базу, сделайте в ней шаги из «Отзывы доступа после копии» ниже, начиная с удаления сессий.
- Снимки Daily, Weekly и Monthly Railway восстанавливает в тот же сервис: после **Deploy** сайт сразу работает на старых данных, и сделать что-то до переключения не получится. Поэтому для отката берите PITR. Снимок — только если нужная минута старше окна PITR; тогда шаги «Отзывы доступа после копии» делайте сразу после Deploy.
- **Нет проекта, тома или доступа к Railway** — внешняя копия, ниже.

### База

Делается на своём компьютере с Docker.

1. **Новая база.** Новый Postgres той же версии, например новый сервис в Railway. Временно включите ему публичное подключение (Settings → Networking → TCP Proxy) и возьмите адрес `postgresql://USER:PASSWORD@HOST:PORT/railway`. Пароль из него пойдёт в `restore.env`, а в команду — адрес без пароля: `postgresql://USER@HOST:PORT/railway`.
2. **Файл `restore.env`** в `~/visacrm-keys`, не в репозитории:

   ```
   BACKUP_S3_ENDPOINT=https://s3.eu-central-003.backblazeb2.com
   BACKUP_S3_REGION=eu-central-003
   BACKUP_S3_BUCKET=<имя бакета>
   BACKUP_S3_ACCESS_KEY=<keyID ключа Read Only>
   BACKUP_S3_SECRET_KEY=<applicationKey этого ключа>
   BACKUP_AGE_IDENTITY=/keys/visacrm-backup-key.txt
   PGPASSWORD=<пароль новой базы>
   ```

   Пароль базы указывается только здесь. Адрес с паролем `restore.sh` не примет: адрес стоит в командной строке, а её видят все программы на компьютере.
3. **Восстановление.** Закрытый ключ age временно положите в `~/visacrm-keys` (вне репозитория), туда же `restore.env`. Команды — из папки репозитория:

   ```bash
   docker build -t visacrm-backup ops/backup
   docker run --rm --env-file ~/visacrm-keys/restore.env -v ~/visacrm-keys:/keys:ro --entrypoint restore.sh \
     visacrm-backup latest "postgresql://USER@HOST:PORT/railway"
   ```

   В PowerShell — `"$HOME\visacrm-keys\restore.env"` и `"$HOME\visacrm-keys:/keys:ro"`, а перенос строки — обратная кавычка `` ` `` вместо `\`.

   Вместо `latest` можно указать конкретную копию: `daily/visacrm-20261101T010000Z.dump.age` или `monthly/…`. Список копий — в B2, в бакете.

   `restore.sh` восстанавливает только в пустую базу: если в ней уже есть таблицы, он откажется. Перезаписать живые данные он не может. Сессии входа из копии он удаляет: после восстановления все входят заново.

   **Какие копии берёт `restore.sh`.** Ключ бэкапа `railway-backup-db` лежит в Railway и может писать в `db/`, поэтому `restore.sh` берёт только копии, похожие на сделанные `backup.sh`:
   - имя вида `visacrm-20261101T010000Z.dump.age`;
   - время в имени не в будущем;
   - B2 принял файл в течение 6 часов после этого времени (по часам B2);
   - у файла одна версия.

   `latest` — самая новая такая копия в `db/daily/`. Остальные файлы он пропускает и пишет `restore: skipped …` с причиной. Копию с двумя версиями (под её именем загрузили другой файл) он не восстанавливает ни через `latest`, ни по имени. Такие строки почти наверняка значат, что в бакет писал кто-то чужой. Восстановите более раннюю копию по имени. Потом замените оба ключа сервиса (раздел 2, шаг 5: удалите их `b2 key delete <keyID>` и создайте заново) и проверьте, у кого есть доступ к переменным сервиса `backup` в Railway.

   **Копии пропали.** Если `restore.sh` пишет, что копий нет, а в B2 они видны при показе скрытых файлов, их скрыл кто-то с ключом из Railway. Удалить их он не мог: копия цела, пока не кончилась её блокировка (у ежедневной — 30 дней от загрузки, у ежемесячной — 366). Верните нужную копию B2 CLI под мастер-ключом: `b2 file unhide b2://<имя бакета>/db/daily/<имя копии>` (синтаксис — `b2 file unhide --help`). Ключи замените, как сказано выше.
4. **Отзывы доступа.** До переключения сделайте шаги из «Отзывы доступа после копии» ниже. Время копии `restore.sh` печатает в последней строке.
5. **Переключение.** Веб-сервису поставьте `DATABASE_URL` на новую базу. `VAULT_MASTER_KEY` нужен тот же, что был в production. Выключите TCP Proxy, удалите ключ age из папки `keys` и пароль из `restore.env`.

### Отзывы доступа после копии

Копия помнит доступы такими, какими они были в момент копии. Всё, что отозвали позже, в новой базе снова действует: заблокированный сотрудник или визовик входит по старому паролю, сброшенный пароль снова подходит, стёртый кабинет снова со всеми данными. Поэтому, пока сайт не переключён на новую базу:

1. **Время копии.** Оно в имени копии: `visacrm-20261101T010000Z` — это 1 ноября 2026, 01:00 UTC. У PITR — выбранная минута.
2. **Сессии.** После внешней копии их уже удалил `restore.sh`. После PITR удалите их в новой базе сами (новый сервис Postgres → **Data** → Query):

   ```sql
   DELETE FROM user_sessions;
   ```
3. **Что отозвали после копии.** Если старая база ещё открывается (после PITR она цела), выполните в ней запрос, подставив время копии:

   ```sql
   SELECT occurred_at, action, tenant_id, actor_id, target_id
   FROM audit_events
   WHERE occurred_at > '2026-11-01 01:00:00+00'
     AND action IN ('employees.blocked', 'employees.password_reset',
       'admin.specialist_blocked', 'admin.specialist_password_reset',
       'auth.password_changed', 'auth.two_factor_disabled', 'auth.two_factor_reset',
       'workspace.erased')
   ORDER BY occurred_at;
   ```

   Сохраните результат у себя: этих записей журнала в новой базе не будет.
4. **Повторите отзывы в новой базе**, по каждой строке результата:

   | Событие | Что выполнить в новой базе |
   | --- | --- |
   | `employees.blocked` | `UPDATE users SET is_active = false WHERE id = '<target_id>';` |
   | `admin.specialist_blocked`, `workspace.erased` | `UPDATE tenants SET is_active = false WHERE id = '<tenant_id>';` |
   | `employees.password_reset`, `auth.two_factor_reset` | `UPDATE users SET password_hash = NULL WHERE id = '<target_id>';` |
   | `admin.specialist_password_reset` | `UPDATE users SET password_hash = NULL WHERE tenant_id = '<tenant_id>' AND role = 'specialist';` |
   | `auth.password_changed`, `auth.two_factor_disabled` | `UPDATE users SET password_hash = NULL WHERE id = '<actor_id>' AND role <> 'admin';` |

   После переключения:
   - Без пароля войти нельзя, пока не выдадут новый временный. Сотруднику его выдаёт визовик («Сотрудники» → «Новый пароль»), визовику — админ («Визовики» → «Новый пароль»). Новый пароль сбрасывает и двухфакторный вход.
   - Стёртый кабинет админ разблокирует, когда визовик готов, и визовик сразу удаляет данные снова: «Настройки» → «Удалить все данные…».
   - Админ, который сменил свой пароль после копии, входит старым и сразу меняет его снова.
5. **Если списка нет** (старая база не открывается, своих записей нет), заблокируйте всех сотрудников:

   ```sql
   UPDATE users SET is_active = false WHERE role = 'employee';
   ```

   После переключения визовики разблокируют своих сотрудников сами. Визовикам, которые меняли пароль после копии, админ выдаёт новый временный.

### Файлы

1. Создайте новый бакет файлов, если старого нет (deploy.md → «Хранилище файлов»).
2. Допишите в `restore.env` его `S3_*`.
3. Запустите:

   ```bash
   docker run --rm --env-file ~/visacrm-keys/restore.env --entrypoint sh visacrm-backup \
     -c '. /usr/local/bin/remotes.sh && rclone copy "dest:$BACKUP_S3_BUCKET/files" "files:$S3_BUCKET"'
   ```

## 5. Проверка раз в месяц

Копия, которую ни разу не восстанавливали, — не копия.

В проверочной базе настоящие данные клиентов всех кабинетов, поэтому она живёт в отдельном контейнере Postgres без сети и без порта. По сети к ней не подключиться ни из локальной сети, ни с этого компьютера, только через сам Docker (`docker exec`). `restore.sh` достаёт до неё через сокет в общем томе Docker. Пароль базы случайный, он нигде не нужен. База из `docker compose` для проверки не годится: её пароль лежит в репозитории.

1. **Пустая база.** Postgres 18, как в production: копию от `pg_dump` 18 в базу младшей версии не восстановить.

   ```bash
   docker run -d --name visacrm-restore-drill --network none \
     -v visacrm-restore-drill-socket:/var/run/postgresql \
     -e POSTGRES_PASSWORD="$(openssl rand -hex 16)" -e POSTGRES_DB=restore_drill \
     postgres:18 -c listen_addresses=
   ```

   В PowerShell пароль — `-e "POSTGRES_PASSWORD=$([guid]::NewGuid())"`, а перенос строки — обратная кавычка `` ` `` вместо `\`. База готова, когда `docker logs visacrm-restore-drill` показывает `PostgreSQL init process complete`.
2. **Восстановление.** Как в разделе 4, шаги 2–3. `PGPASSWORD` в `restore.env` не нужен, а к команде добавляется том с сокетом:

   ```bash
   docker run --rm --env-file ~/visacrm-keys/restore.env -v ~/visacrm-keys:/keys:ro \
     -v visacrm-restore-drill-socket:/var/run/postgresql --entrypoint restore.sh \
     visacrm-backup latest "postgresql:///restore_drill?host=/var/run/postgresql"
   ```
3. **Сверка.** Число клиентов и кейсов совпадает с тем, что видно в CRM на день копии, а сессий входа — 0:

   ```bash
   docker exec visacrm-restore-drill psql -U postgres restore_drill -c "SELECT (SELECT count(*) FROM contacts) AS contacts, (SELECT count(*) FROM visa_cases) AS cases, (SELECT count(*) FROM user_sessions) AS sessions"
   ```
4. **Удаление.** Удалите контейнер вместе с базой (`-v` удаляет и её том), том с сокетом и ключ с диска:

   ```bash
   docker rm -f -v visacrm-restore-drill
   docker volume rm visacrm-restore-drill-socket
   ```
5. **Запись.** Запишите строку ниже.

| Дата | Копия | Результат |
| --- | --- | --- |
| 2026-10-26 | `daily/visacrm-20261026T010124Z.dump.age` | Восстановлена в пустую базу Postgres 18 (`docker compose`); число визовиков, клиентов и кейсов совпало с production |

## 6. Ключи с наименьшими правами: проверка и замена

До задачи S21 у сервиса `backup` был один ключ `railway-backup` типа Read and Write. С ним можно было навсегда удалить ежемесячные копии и документы в зеркале, как только кончались их 30 дней блокировки, обойти блокировку Governance, а ещё сократить блокировку по умолчанию и правила хранения для новых копий. Теперь у сервиса два ключа из раздела 2, шаг 5. Сначала они проверяются на отдельном тестовом бакете, потом заменяют старый ключ в production.

### Проверка на тестовом бакете

Production-бакет, его ключи и Railway в проверке не участвуют. Нужны Docker, запущенный `docker compose` (из его базы делается копия для проверки) и B2 CLI из раздела 2. Команды — из папки репозитория. В PowerShell `~/visacrm-keys` пишется как `"$HOME\visacrm-keys\…"`.

1. **Тестовый бакет.** Как в разделе 2, шаги 2–3, но с именем `visacrm-backup-test-<что-то своё>` и блокировкой на **1** день. Правила жизненного цикла не нужны.
2. **Тестовые ключи.** Как в разделе 2, шаг 5, но с `--bucket` тестового бакета и именами `test-backup-db` и `test-backup-files`.
3. **Настройки** — три файла в `~/visacrm-keys`, вне репозитория. `s21-backup.env`:

   ```
   DATABASE_URL=postgresql://visacrm@db:5432/visacrm
   PGPASSWORD=visacrm
   BACKUP_AGE_RECIPIENT=age1…
   BACKUP_S3_ENDPOINT=https://s3.eu-central-003.backblazeb2.com
   BACKUP_S3_REGION=eu-central-003
   BACKUP_S3_BUCKET=visacrm-backup-test-<…>
   BACKUP_S3_DB_ACCESS_KEY=<keyID ключа test-backup-db>
   BACKUP_S3_DB_SECRET_KEY=<applicationKey этого ключа>
   BACKUP_FILES=off
   BACKUP_MONTHLY=always
   BACKUP_MONTHLY_LOCK_DAYS=2
   ```

   `PGPASSWORD` здесь — пароль локальной базы из `docker-compose.yml`, `BACKUP_AGE_RECIPIENT` — открытый ключ из раздела 1. Ещё `s21-db.env` и `s21-files.env`, в каждом по пять строк: `BACKUP_S3_ENDPOINT`, `BACKUP_S3_REGION` и `BACKUP_S3_BUCKET` как выше, а `BACKUP_S3_ACCESS_KEY` и `BACKUP_S3_SECRET_KEY` — ключ `test-backup-db` или `test-backup-files`.
4. **Бэкап с новым ключом.** Сеть — та, в которой работает `docker compose` (`docker network ls`, обычно `visa_crm_default`):

   ```bash
   docker build -t visacrm-backup ops/backup
   docker run --rm --network visa_crm_default --env-file ~/visacrm-keys/s21-backup.env visacrm-backup
   ```

   В логе должны быть `backup: db/daily/…` и `backup: db/monthly/…, locked until <через 2 дня>`. В B2 в сведениях о файле `db/monthly/…` — «Object Lock: Compliance Mode» до этой даты, у `db/daily/…` — на день.
5. **Зеркало скрывает, но не удаляет.** Ключ `test-backup-files` дважды синхронизирует папку: сначала два файла, потом один.

   ```bash
   docker run --rm --env-file ~/visacrm-keys/s21-files.env --entrypoint sh visacrm-backup -c '. /usr/local/bin/remotes.sh && mkdir /tmp/a /tmp/b && echo 1 > /tmp/a/one && echo 2 > /tmp/a/two && echo 1 > /tmp/b/one && rclone sync /tmp/a dest:$BACKUP_S3_BUCKET/files/ && rclone sync /tmp/b dest:$BACKUP_S3_BUCKET/files/ && rclone lsf --s3-versions dest:$BACKUP_S3_BUCKET/files/'
   ```

   В конце должно быть `one` и `two-v…`: файл `two` скрыт, его версия на месте. Если второй `rclone sync` отказал (`AccessDenied`, `unauthorized`), значит B2 требует для скрытия ещё право `deleteFiles`. Тогда production не переключайте и запишите вопрос в `docs/open-questions.md`: с `deleteFiles` у ключа зеркала документы в `files/` старше 30 дней снова можно удалить ключом из Railway.
6. **Каждый ключ — только в своей папке.** Обе команды должны закончиться ошибкой доступа:

   ```bash
   docker run --rm --env-file ~/visacrm-keys/s21-db.env --entrypoint sh visacrm-backup -c '. /usr/local/bin/remotes.sh && echo x > /tmp/x && rclone copyto /tmp/x dest:$BACKUP_S3_BUCKET/files/x'
   docker run --rm --env-file ~/visacrm-keys/s21-files.env --entrypoint sh visacrm-backup -c '. /usr/local/bin/remotes.sh && echo x > /tmp/x && rclone copyto /tmp/x dest:$BACKUP_S3_BUCKET/db/daily/x'
   ```
7. **Через 2 дня, когда блокировка кончится, ключи всё равно не удаляют.** Каждым ключом попробуйте удалить все версии в его папке:

   ```bash
   docker run --rm --env-file ~/visacrm-keys/s21-db.env --entrypoint sh visacrm-backup -c '. /usr/local/bin/remotes.sh; rclone delete --s3-versions dest:$BACKUP_S3_BUCKET/db/; rclone lsf -R --s3-versions dest:$BACKUP_S3_BUCKET/db/'
   docker run --rm --env-file ~/visacrm-keys/s21-files.env --entrypoint sh visacrm-backup -c '. /usr/local/bin/remotes.sh; rclone delete --s3-versions dest:$BACKUP_S3_BUCKET/files/; rclone lsf -R --s3-versions dest:$BACKUP_S3_BUCKET/files/'
   ```

   Удаление версий должно закончиться ошибками доступа, а список в конце — показать все файлы. Файлы при этом могут стать скрытыми: это право `writeFiles`, их версии остаются. Если какая-то версия исчезла, у ключа есть право удалять: проверьте его в `b2 key list --long`.
8. **Уборка.** Удалите тестовые ключи (`b2 key delete <keyID>`, потом `b2 account clear`), файлы `s21-*.env` и тестовый бакет вместе с файлами (веб-интерфейс, у бакета **Delete Bucket**).

### Замена старого ключа в production

Порядок такой, чтобы ни один ночной запуск не остался без копии: пока у сервиса есть старые переменные, `backup.sh` до S21 работает с ними, а после S21 берёт новые ключи.

1. **B2.** Создайте `railway-backup-db` и `railway-backup-files` для production-бакета (раздел 2, шаг 5).
2. **Railway**, сервис `backup` → **Variables**: добавьте `BACKUP_S3_DB_ACCESS_KEY`, `BACKUP_S3_DB_SECRET_KEY`, `BACKUP_S3_FILES_ACCESS_KEY` и `BACKUP_S3_FILES_SECRET_KEY`. Старые `BACKUP_S3_ACCESS_KEY` и `BACKUP_S3_SECRET_KEY` пока не трогайте.
3. **Проверка новых ключей.** Когда в Railway выкатился код с S21, запустите сервис вручную (раздел 3, шаг 5). В логе должны быть `backup: db/daily/…` и `backup: files mirrored, …`, а последней строкой — `the copy is made, but the service still holds BACKUP_S3_ACCESS_KEY`. Запуск красный, и так и задумано: копия сделана новыми ключами, осталось убрать старый.
4. **Railway.** Удалите у сервиса `BACKUP_S3_ACCESS_KEY` и `BACKUP_S3_SECRET_KEY` и запустите его ещё раз: теперь запуск зелёный.
5. **B2.** Удалите старый ключ `railway-backup`: в веб-интерфейсе **Application Keys** → **Delete** у него, или `b2 key delete <keyID>` (keyID — в `b2 key list`).
6. **B2.** Проверьте ключ `restore` (раздел 2, шаг 6) и поставьте лимит (шаг 7).
7. **1-го числа** в логе будет `backup: db/monthly/…, locked until …`, а в B2 в сведениях о файле — «Object Lock: Compliance Mode» до этой даты.

Ежемесячные копии, сделанные до замены, заблокированы только на 30 дней. Но после замены удалить их ключом из Railway уже нельзя: права `deleteFiles` нет ни у одного из ключей.
