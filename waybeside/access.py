"""助手命令行 api 通道的路由白名单：按 Flask endpoint 名枚举。新路由必须归入其中一类，否则测试失败。"""

# 助手（full 方式下）可经 api 调用的端点。
AGENT_ENDPOINTS = frozenset({
    'get_state', 'observation', 'auxiliary', 'summary',
    'save_course', 'generate_sessions', 'create_session', 'update_session',
    'get_settings', 'onboard',
    'menu_state', 'action', 'shopping', 'import_preview', 'import_confirm',
    'candidate_state', 'candidate_adopt', 'candidate_decline',
})

# 只能由人在网页上做、或走专门子命令的端点。
EXCLUDED_ENDPOINTS = frozenset({
    'login', 'session', 'exit_service', 'accept_disclaimer',
    'get_agent_share', 'set_agent_share', 'set_assistant_mode', 'export_backup', 'assistant_activity', 'seen_upgrade_notice',
})
