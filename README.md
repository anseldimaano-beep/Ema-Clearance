# EMA Clearance
Web-Based Digital Clearance and Approval System with Payment Tracking for EMA EMITS College.

## Run
    docker compose up --build -d
    # first start auto-creates the registrar account and loads data/students.csv (162 students)
    docker compose exec web python manage.py seed      # OPTIONAL demo teachers/heads/cashier
Open http://localhost:8000 and sign in as **registrar** / the REGISTRAR_PASSWORD in docker-compose.yml (change it!). Registrar > Students > Download login list gives every student their initial password (one time). Demo logins (password `ema12345`): student1, t_math, t_sci, head_research, head_instruction, cashier, registrar.

## Passwords
Copy `.env.example` to `.env` and set `SECRET_KEY`, `REGISTRAR_PASSWORD`, `ADMIN_PASSWORD` before the first start (the accounts are created once, on first start). `docker compose up` warns when a default is still in use.

## Flow
1. Student attaches the **Student ID (front and back; the back shows the semester validation sticker)** and the **Registration Card (student's copy)** (photo or PDF) and taps **Request clearance** -> it goes to the Registrar (status: Requested). The Registrar is notified.
2. Registrar opens **Requests**, checks the ID, its validation sticker and the Registration Card, then **Release** (or **Return** with a reason; the student can request again). **Release all** does the whole queue at once.
3. Releasing notifies every account that has to act (their teachers, offices, the Cashier) plus Owner and Admin, via the bell and email if the account has an email address. Students are notified on their own events (see *Alerts*).
4. Teachers and offices sign in their inbox, then the Registrar signs once everyone else has.
5. The **Cashier signs last**, once every other signature is in and the balance is 0 (the Cashier is notified when it is their turn). The student prints the certificate (verify at /verify/<code>/).

Email: set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend plus EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, DEFAULT_FROM_EMAIL. Without it, emails print to the server log and the in-app bell still works.

## Subjects (BSCS for now)
The BSCS curriculum (from the paper clearance form) is loaded from `data/curriculum.csv`. When a BSCS student requests a clearance they tick the subjects they take now (their year and the open semester are pre-ticked). Each ticked subject goes to that subject's teacher. Set the semester on Clearance period, and assign teachers with Students > Assign a teacher to a class (pick the subject code). Other programs use the old behaviour until you add their rows to the CSV (or in /admin > Curriculum subjects).

**Irregular / transferee students:** the request form has an *Other subject* box (comma-separated codes) for subjects not in the curriculum. After release, the student can use **➕ Add a subject** on their page (pick from the program list or type a code). The Registrar and the teacher (if assigned) are notified; with no teacher yet, the Registrar assigns one under Students.

## Accounts and privileges
Create accounts in /admin (User > Role, plus Office / Scope / Program where noted). Signing offices use Role = "Signing Office".

| Account | Role / Office | Can do |
|---|---|---|
| System Admin | admin | Everything setup-related: /admin, Students, Payments (view), Overview. Does not sign. |
| School Owner | owner | Read-only Overview (progress by program, where signatures are stuck, money collected/outstanding). |
| Registrar | registrar | Students (import, set program, assign teachers), view Payments, Overview, signs after all teachers and offices, just before the Cashier. |
| Cashier | cashier | Sets assessments, records payments. Signs LAST (final signature), only when every other signature is in and the balance is 0. |
| Dept Heads (BSCS, BEED, BSBA, BSHM) | office = dept_head, Scope = program | Signs only students of their program. |
| College Dean | office = dean | Signs all students; also sees Overview. |
| Dean of Student Affairs | office = dsa | Signs all students. |
| Director of Research / Instruction | office = dir_research / dir_instruction | Signs all students. |
| Director of Pre-Service Teaching | office = dir_preservice | Signs all students (it is on the BSCS clearance form too). |
| INTRAMS | office = intrams | Signs all students. |
| Librarian | office = librarian | Signs all students. |
| ELPT Office | office = elpt | Signs all students. |
| EEMG President | office = eemg | Signs all students. |
| Subject Teacher | teacher | Signs own subject for own students; online gradebook with missing-grade detection. |
| Student | student | Requests clearance, sees progress/payments, prints certificate. |

If the Department Head does not sign your clearance form, set `REQUIRE_DEPT_HEAD=0` in docker-compose.yml.
Set each class's program in Registrar > Students > "Set program for a class". New teacher loads or a newly set program add the missing signatures to existing clearances automatically.

## Creating accounts
- **Students (self-service):** "Create an account" on the login page (student number, name, program, class, password). The account stays inactive until the Registrar approves it under Students > Account requests. If the name matches a roster record, "Approve & link to roster" merges them so the student's real student number becomes their login.
- **Staff and anyone else:** Admin or Registrar > **Accounts** > Create an account. A random password is shown once. The same page resets passwords and deactivates accounts. The Registrar can create students, teachers, offices and the Cashier; only the Admin can create Admin, Owner and Registrar accounts.
- Only the Admin account has /admin access. If you ran an earlier version, untick Staff/Superuser on the registrar account in /admin.

## Day-to-day tools (Registrar)
- **Signing inbox**: search by name/number, filter by subject, program and status. Rejected items stay visible until fixed. Tick students (or *Select all ready*) and **Sign selected** to sign in bulk. Items whose prerequisites are unmet, or that have gradebook issues, cannot be bulk-signed. Rejecting needs a reason. **↩ Reopen** makes a rejected item pending again.
- **All clearances** (Registrar/Admin): search every student, **Reopen** any signature, **Credit** a subject for a transferee (no teacher signature needed), **Remove** a wrong subject, **Export CSV**.
- **Audit log**: who signed, rejected, reopened, credited, released, returned or recorded a payment, and when.
- **Requests**: search/filter; *Release these* only releases what is shown.

## Alerts
Students get an in-app bell notice (plus email if set on **Profile**, plus SMS if a mobile number is saved and `SMS_API_KEY` is set) when their clearance is released, returned, a signature is rejected, only the Cashier is left, and when they are cleared. Signers get a reminder once a day for signatures waiting more than `REMIND_AFTER_DAYS` (3) days. It runs on the first login of each day, or run `python manage.py send_reminders` from cron. SMS uses Semaphore (semaphore.co, Philippines).

## Backups
A database copy is saved to `/data/backups` (newest 14 kept) at every start and on the first login of each day. Admin can also click **Backup** in the menu to download one. Uploaded ID photos are in `/data/media`; back up that folder too. Restore: stop the app, copy a backup over `/data/db.sqlite3`, start.

## Other settings
- `OFFICES_NOT_FOR="dir_preservice:BSBA|BSHM"`: that office does not sign for those programs (new clearances).
- Students can switch the page to Filipino (menu > Filipino). Staff pages are English.

## Using Neon (PostgreSQL) instead of SQLite
1. At neon.tech create a project, then **Connect** and copy the connection string (`postgresql://...?sslmode=require`).
2. Put it in `.env` as `DATABASE_URL=...` (on Render: add it as an environment variable). Start the app: it creates the tables itself.
3. To keep existing data, before switching run on the old setup:
   `python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission -e admin.logentry -e sessions -o data.json`
   then, with `DATABASE_URL` set: `python manage.py migrate` and `python manage.py loaddata data.json`.
4. Backups are then Neon's job (Restore / branches in the Neon console); the in-app Backup button and the local backup files turn off.
Uploaded ID photos are not stored in Neon: they stay in the `/data/media` volume/disk.

## Deploy on Render
1. Push this folder to a GitHub repo.
2. Render > New > Blueprint > pick the repo (uses `render.yaml`; for the free plan set the Blueprint path to `render.free.yaml`).
3. Fill REGISTRAR_PASSWORD, ADMIN_PASSWORD (and DATABASE_URL from Neon if used) when asked, then Apply.
4. Open the `https://ema-clearance.onrender.com` URL once the deploy is live; sign in as `registrar`.
