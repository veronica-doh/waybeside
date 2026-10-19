ALTER TABLE profile ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE habit ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
CREATE TABLE plan_input(target_date TEXT PRIMARY KEY, content TEXT NOT NULL, version INTEGER NOT NULL);
CREATE TABLE job(id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, target_date TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL, snapshot TEXT NOT NULL, candidate TEXT, error TEXT, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX one_running_job ON job(subject_id,target_date) WHERE status IN ('QUEUED','RUNNING','VALIDATING');
CREATE TABLE plan_version(id INTEGER PRIMARY KEY, subject_id TEXT NOT NULL, target_date TEXT NOT NULL, version INTEGER NOT NULL, parent_id INTEGER REFERENCES plan_version(id), reverted_from INTEGER REFERENCES plan_version(id), job_id TEXT REFERENCES job(id), content TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(subject_id,target_date,version));
