ALTER TABLE observation ADD COLUMN recorded_by TEXT NOT NULL DEFAULT 'local-user';
UPDATE observation SET content=json_set(content,'$.labs',json((SELECT json_group_array(json_set(value,'$.recorded_by','local-user')) FROM json_each(observation.content,'$.labs')))) WHERE kind='aux' AND json_type(content,'$.labs')='array';
INSERT OR IGNORE INTO metadata(key,value) SELECT 'assistant_mode', CASE WHEN EXISTS(SELECT 1 FROM metadata WHERE key='disclaimer_ack') THEN 'menu_only' ELSE 'unset' END;
INSERT OR IGNORE INTO metadata(key,value) VALUES('assistant_mode_version','0');
INSERT OR IGNORE INTO metadata(key,value) SELECT 'upgrade_notice','pending' WHERE EXISTS(SELECT 1 FROM metadata WHERE key='disclaimer_ack') AND (SELECT value FROM metadata WHERE key='assistant_mode')='menu_only';
