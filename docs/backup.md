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
  - `Dockerfile` — образ на основе `postgres` нужной версии с `age` и `rclone`.
- **Шифрование.** Копия базы шифруется [age](https://age-encryption.org) открытым ключом. Закрытый ключ есть только у владельца, в Railway его нет. Даже с доступом к Railway и к B2 копию не прочитать.
  - Файлы клиентов и так зашифрованы ключом визовика (`backend/app/core/documents.py`) и копируются как есть.
  - Незашифрованное на диск не пишется.
- **Защита от удаления.** Бакет в B2 создаётся с блокировкой удаления (Object Lock): 30 дней копию не может удалить никто, даже владелец ключей.
- **Сроки хранения** задают правила жизненного цикла бакета:

  | Что | Папка | Срок |
  | --- | --- | --- |
  | ежедневные копии | `db/daily/` | 30 дней |
  | ежемесячные копии (за 1-е число) | `db/monthly/` | 12 месяцев |
  | файлы, удалённые в CRM | `files/` | 30 дней после удаления |

- **Проверка.** На каждом PR задание CI `backup` проходит весь путь на демо-данных: бэкап → проверка, что копия зашифрована → восстановление в пустую базу → сверка каждой таблицы по содержимому, плюс RLS, политики, права и триггеры. Раз в месяц владелец проверяет настоящую копию (раздел 5).
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
3. **Блокировка удаления.** В списке бакетов нажмите у бакета **Object Lock: Enabled**, укажите срок **30** дней и нажмите **Save changes**. Срок действует для файлов, загруженных после сохранения, поэтому задайте его до первого запуска бэкапа.
4. **Сроки хранения.** **Lifecycle Settings** → **Use custom lifecycle rules**, три правила. Каждое — со своим **File Path**, поле дней, которого нет в таблице, оставьте пустым. Сохраните кнопкой **Update Bucket**. Правило с пустым File Path действует на весь бакет — такого не делайте.

   | File Path | Days from uploading to hiding | Days from hiding to deleting |
   | --- | --- | --- |
   | `db/daily/` | 30 | 1 |
   | `db/monthly/` | 365 | 1 |
   | `files/` | — | 30 |

5. **Ключ для бэкапа.** **Application Keys** → **Add a New Application Key**:
   - Name of Key — `railway-backup`;
   - Allow access to Bucket(s) — только этот бакет;
   - Type of Access — **Read and Write** (удаление нужно зеркалу файлов; заблокированные копии этот ключ всё равно удалить не может);
   - **Allow List All Bucket Names** — отметьте: без неё ключ одного бакета получает ошибку авторизации на части запросов S3;
   - File name prefix и Duration — пусто.

   Сохраните `keyID` и `applicationKey`: второй показывается один раз.
6. **Ключ для восстановления.** Второй ключ, `restore`: тот же бакет, тип **Read Only**, та же галочка. Его храните рядом с закрытым ключом age, в Railway он не нужен.
7. **Адрес бакета.** На странице бакета указан **Endpoint**, например `s3.eu-central-003.backblazeb2.com`; его средняя часть — регион (`eu-central-003`).

## 3. Сервис `backup` в Railway

Только в окружении **production**. В `staging` сервис не создавайте: его копировал бы каждый стенд PR.

1. **Версия Postgres.** Postgres → **Data** → Query: `SHOW server_version;`. Если первая цифра не 18, позже понадобится переменная `PG_MAJOR` (шаг 4): `pg_dump` должен быть той же версии, что сервер, или новее.
2. **Создание сервиса.** **+ Create → GitHub Repo** → этот репозиторий. Назовите сервис `backup`.
3. **Settings:**
   - Source → **Root Directory**: `ops/backup` (Railway соберёт `ops/backup/Dockerfile`);
   - Deploy → **Cron Schedule**: `0 1 * * *` — каждый день в 01:00 UTC (04:00 по Минску);
   - Deploy → Restart Policy: **Never**; Healthcheck Path — пусто;
   - Networking — домен не создавайте: сервису не нужен вход снаружи.

   Если в настройках появились `Healthcheck Path /api/health` или команда запуска веб-сервиса, их подтянул общий `railway.json` — уберите.
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
   BACKUP_S3_ACCESS_KEY=<keyID ключа Read and Write>
   BACKUP_S3_SECRET_KEY=<applicationKey этого ключа>
   BACKUP_AGE_RECIPIENT=age1…
   ```

   - `Postgres` и `Files` — имена сервиса базы и бакета, как в deploy.md.
   - Если бакета файлов ещё нет, вместо `S3_*` поставьте `BACKUP_FILES=off`. Когда бакет появится, верните `S3_*` и уберите `BACKUP_FILES`.
   - `PG_MAJOR=<версия>` — только если на шаге 1 вышла не 18.
   - `BACKUP_HEARTBEAT_URL` — по желанию: адрес сервиса-наблюдателя (например, healthchecks.io). Скрипт обращается к нему после каждой успешной копии, и наблюдатель пишет вам, если копии перестали приходить.
5. **Первый запуск.** **Deploy**. В логах должно быть `backup: db/daily/visacrm-….dump.age, N bytes` и `backup: files mirrored, …`. В B2 в бакете появятся `db/daily/` и `files/`. Если запуск красный, причина — в последних строках лога: `… is not set`, ошибка `pg_dump` или `rclone`.

## 4. Восстановление

Сначала решите, какой слой нужен:

- **Ошибка в данных** (неудачная миграция, удалили лишнее), а проект Railway цел — восстановление Railway: Postgres → **Backups** → PITR на нужную минуту (deploy.md, раздел 7). Это быстрее и точнее суточной копии.
- **Нет проекта, тома или доступа к Railway** — внешняя копия, ниже.

### База

Делается на своём компьютере с Docker.

1. **Новая база.** Новый Postgres той же версии, например новый сервис в Railway. Временно включите ему публичное подключение (Settings → Networking → TCP Proxy) и возьмите адрес `postgresql://…`.
2. **Файл `restore.env`** в `~/visacrm-keys`, не в репозитории:

   ```
   BACKUP_S3_ENDPOINT=https://s3.eu-central-003.backblazeb2.com
   BACKUP_S3_REGION=eu-central-003
   BACKUP_S3_BUCKET=<имя бакета>
   BACKUP_S3_ACCESS_KEY=<keyID ключа Read Only>
   BACKUP_S3_SECRET_KEY=<applicationKey этого ключа>
   BACKUP_AGE_IDENTITY=/keys/visacrm-backup-key.txt
   ```
3. **Восстановление.** Закрытый ключ age временно положите в `~/visacrm-keys` (вне репозитория), туда же `restore.env`. Команды — из папки репозитория:

   ```bash
   docker build -t visacrm-backup ops/backup
   docker run --rm --env-file ~/visacrm-keys/restore.env -v ~/visacrm-keys:/keys:ro --entrypoint restore.sh \
     visacrm-backup latest "postgresql://USER:PASSWORD@HOST:PORT/railway"
   ```

   В PowerShell — `"$HOME\visacrm-keys\restore.env"` и `"$HOME\visacrm-keys:/keys:ro"`, а перенос строки — обратная кавычка `` ` `` вместо `\`.

   Вместо `latest` можно указать конкретную копию: `daily/visacrm-20261101T010000Z.dump.age` или `monthly/…`. Список копий — в B2, в бакете.

   `restore.sh` восстанавливает только в пустую базу: если в ней уже есть таблицы, он откажется. Перезаписать живые данные он не может.
4. **Переключение.** Веб-сервису поставьте `DATABASE_URL` на новую базу. `VAULT_MASTER_KEY` нужен тот же, что был в production. Выключите TCP Proxy, удалите ключ age из папки `keys`.

### Файлы

1. Создайте новый бакет файлов, если старого нет (deploy.md → «Хранилище файлов»).
2. Допишите в `restore.env` его `S3_*`.
3. Запустите:

   ```bash
   docker run --rm --env-file ~/visacrm-keys/restore.env --entrypoint sh visacrm-backup \
     -c '. /usr/local/bin/remotes.sh && rclone copy "dest:$BACKUP_S3_BUCKET/files" "files:$S3_BUCKET"'
   ```

## 5. Проверка раз в месяц

Копия, которую ни разу не восстанавливали, — не копия. Проверка идёт на своём компьютере с Docker Desktop, во временной базе, которая потом удаляется; production она не трогает. Около 15 минут.

1. **Ключи в папке `visacrm-keys`** в домашнем каталоге (`%USERPROFILE%\visacrm-keys` в Windows), не в репозитории:
   - `visacrm-backup-key.txt` — закрытый ключ age из менеджера паролей, целиком (строки `# created`, `# public key`, `AGE-SECRET-KEY-1…`), в кодировке UTF-8;
   - `restore.env` — как в разделе 4, с ключом B2 `restore` (Read Only). В Блокноте — «Тип файла: Все файлы», иначе он сохранит `restore.env.txt`.

   Ошибка `SignatureDoesNotMatch` от B2 — неверный `BACKUP_S3_SECRET_KEY`: у ключа B2 `keyID` длиной 25 символов, `applicationKey` — 31, без пробелов в конце.
2. **Образ и пустая база** — из папки репозитория:

   ```bash
   docker build -t visacrm-backup ops/backup
   docker network create restore-net
   docker run -d --name restore-db --network restore-net -e POSTGRES_USER=visacrm -e POSTGRES_PASSWORD=visacrm -e POSTGRES_DB=restore_drill postgres:18
   ```
3. **Восстановление последней копии** (в PowerShell — `"$HOME\visacrm-keys\restore.env"` и `"$HOME\visacrm-keys:/keys:ro"`):

   ```bash
   docker run --rm --network restore-net --env-file ~/visacrm-keys/restore.env -v ~/visacrm-keys:/keys:ro --entrypoint restore.sh visacrm-backup latest postgresql://visacrm:visacrm@restore-db:5432/restore_drill
   ```

   В конце — `restore: db/daily/visacrm-….dump.age restored, N tables`.
4. **Сверка.** Число клиентов и кейсов по кабинетам совпадает с CRM на время копии (04:00 по Минску): клиенты — вкладка «Все» в «Клиентах», кейсы — самый большой номер кейса.

   ```bash
   docker exec restore-db psql -U visacrm -d restore_drill -c "SELECT t.name AS cabinet, (SELECT count(*) FROM contacts c WHERE c.tenant_id = t.id) AS clients, (SELECT count(*) FROM visa_cases v WHERE v.tenant_id = t.id) AS cases FROM tenants t ORDER BY t.name"
   ```
5. **Уборка.** Удалите временную базу и с диска — ключ и `restore.env`: в базе настоящие данные клиентов.

   ```bash
   docker rm -f -v restore-db
   docker network rm restore-net
   ```
6. **Запись.** Добавьте строку в таблицу.

| Дата | Копия | Результат |
| --- | --- | --- |
| 26.10.2026 | `daily/visacrm-20261026T010124Z.dump.age` | Восстановлена, 46 таблиц; 2 кабинета, 2 клиента, 0 кейсов — совпало с CRM |
