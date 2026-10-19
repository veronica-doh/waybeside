"""--demo 的虚构数据：化疗、手动排期、6 个周期、2031 年起。只经 --demo 写入，且只写一次。"""
from datetime import date, timedelta
from .storage import connect, initialize, audit, encode
from .menu_library import active, import_library, synthetic_library

START = date(2031, 3, 3)
CYCLES = 6
INTERVAL_DAYS = 21

def populate(path, today=None):
    initialize(path)
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        fresh = not db.execute("SELECT 1 FROM metadata WHERE key='demo'").fetchone()
        if fresh:
            seed(db, today)
        has_library = active(db) is not None
    if not has_library:
        import_library(path, synthetic_library())
    return fresh

def seed(db, today=None):
    db.execute("INSERT INTO treatment_course(id,treatment_type,label,count_unit,total_count,schedule_kind,weekdays,start_date,version) "
               "VALUES(1,'chemotherapy','演示疗程（虚构）','cycle',?, 'manual','[]',?,1)", (CYCLES, START.isoformat()))
    for n in range(CYCLES):
        day = (START + timedelta(days=INTERVAL_DAYS * n)).isoformat()
        identifier = db.execute("INSERT INTO treatment_session(course_id,ordinal,planned_date,status,confirmation_source,version) VALUES(1,?,?,'planned','demo',1)", (n + 1, day)).lastrowid
        db.execute("INSERT INTO calendar(session_id,date,kind,version) VALUES(?,?,'treatment',1)", (identifier, day))
    # 演示数据也算“已完成首次设置”：基本信息用虚构昵称，其余块标记为已跳过。
    for block, content in (('basic', {'nickname': '演示用户', 'helper_mode': 'self', 'locale': 'zh-CN'}),
                           ('treatment', {'projected': True}), ('diet', {'skipped': True}), ('habit', {'skipped': True})):
        db.execute('INSERT INTO questionnaire_answer(block,content,completed,version) VALUES(?,?,1,1)', (block, encode(content)))
    # 演示检验数据：明确虚构的指标名、单位、实验室与参考区间（不是任何真实检验项目）；日期取“今天”往前，避免被未来日期校验挡住编辑。
    end = date.fromisoformat(today) if today else date.today()
    labs = [{'date': (end - timedelta(days=offset)).isoformat(), 'metric': metric, 'unit': '示例单位', 'lab': '虚构实验室', 'recorded_by': 'local-user', 'value': value, 'low': 10, 'high': 20}
            for metric, values in (('示例指标 A', (12, 15, 18)), ('示例指标 B', (11, 13, 19))) for offset, value in zip((28, 14, 0), values)]
    db.execute("INSERT INTO observation(id,session_id,occurred_at,kind,content,version) VALUES('aux',NULL,?,'aux',?,1)",
               (end.isoformat(), encode({'labs': labs, 'question': '', 'rhythm': {'blood': None, 'chemistry': None}, 'celebration': True})))
    db.execute("INSERT INTO metadata VALUES('demo','1')")
    # 演示库固定为「我自己记，助手只帮挑菜单」。
    db.execute("INSERT INTO metadata VALUES('assistant_mode','menu_only') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    audit(db, 'demo', 'v1', None, {'fictional': True}, actor='demo-import')
