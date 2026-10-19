CREATE TABLE questionnaire_answer(block TEXT PRIMARY KEY CHECK(block IN ('basic','treatment','diet','habit')), content TEXT NOT NULL, completed INTEGER NOT NULL CHECK(completed IN (0,1)), version INTEGER NOT NULL);
CREATE TABLE agent_share(category TEXT PRIMARY KEY CHECK(category IN ('menu_library','adoption_state','preferences','recent_meal_status','requirement_tags','age_band','course_progress')), enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)), version INTEGER NOT NULL);
INSERT INTO agent_share(category,enabled,version) VALUES('menu_library',0,1),('adoption_state',0,1),('preferences',0,1),('recent_meal_status',0,1),('requirement_tags',0,1),('age_band',0,1),('course_progress',0,1);
CREATE TABLE audit_new(id INTEGER PRIMARY KEY, actor TEXT NOT NULL, occurred_at TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT NOT NULL, old_value TEXT, new_value TEXT, detail TEXT);
INSERT INTO audit_new(id,actor,occurred_at,entity,entity_id,old_value,new_value) SELECT id,actor,occurred_at,entity,entity_id,old_value,new_value FROM audit;
DROP TABLE audit;
ALTER TABLE audit_new RENAME TO audit;
