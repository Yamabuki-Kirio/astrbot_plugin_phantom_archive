BEGIN IMMEDIATE;

INSERT INTO source_registry(url_prefix, source_name, source_status, note, row_count)
VALUES (
    'https://www.taptap.cn/moment/870483408487189154',
    '女神异闻录：夜幕魅影官方 / P5X档案室『平野亚兰·凛华』',
    'official',
    '官方五星SP角色资料；2026-11-19已实装。',
    3
)
ON CONFLICT(url_prefix) DO UPDATE SET source_name=excluded.source_name,
    source_status=excluded.source_status, note=excluded.note, row_count=excluded.row_count;

INSERT INTO zh_characters(
    source_key, slug, name_zh, codename_zh, element_zh, position_zh, rarity,
    description_zh, source_status, translation_status, source_language,
    server_region, game_version, field_provenance, source_url, fetched_at
)
VALUES (
    '平野亚兰凛华', 'anri-rinka', '平野亚兰凛华', 'ANRI', '祝福/诅咒', '反抗', 5,
    '五星SP怪盗，初始人格面具为「真蒂莱斯基」。擅长单体攻击，特色机制为双属性伤害、剑盾合一。可造成祝福与诅咒双适应性伤害，所有伤害必定造成weak效果并积累弱点伤害系数；可将己方队友全部适应性暂时升级为「吸」并赋予护盾。5.5.1版本于2026年11月19日登场。',
    'official', 'source_original', 'zh', 'cn', '5.5.1',
    '{"name_zh":{"lang":"zh","status":"source_original"},"codename_zh":{"lang":"zh","status":"source_original"},"element_zh":{"lang":"zh","status":"source_original"},"position_zh":{"lang":"zh","status":"source_original"},"description_zh":{"lang":"zh","status":"source_original"}}',
    'https://www.taptap.cn/moment/870483408487189154', strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
)
ON CONFLICT(source_key) DO UPDATE SET slug=excluded.slug, name_zh=excluded.name_zh,
    codename_zh=excluded.codename_zh, element_zh=excluded.element_zh,
    position_zh=excluded.position_zh, rarity=excluded.rarity,
    description_zh=excluded.description_zh, source_status=excluded.source_status,
    translation_status=excluded.translation_status, source_language=excluded.source_language,
    server_region=excluded.server_region, game_version=excluded.game_version,
    field_provenance=excluded.field_provenance, source_url=excluded.source_url,
    fetched_at=excluded.fetched_at;

INSERT INTO zh_weapons(
    character_key, weapon_key, name_zh, rarity, health, attack, defense,
    skill_name_zh, description_zh, source_status, translation_status,
    source_language, server_region, game_version, field_provenance, source_url
)
VALUES (
    '平野亚兰凛华', '千镜的余冕', '千镜的余冕', NULL, NULL, NULL, NULL, NULL,
    '专属武器。映照自我的镜面碎裂成盾，曾将灵魂囚困的仇恨与镜中倒影，终化作刺向枷锁的锋芒。当前仅收录官方介绍文案。',
    'official', 'source_original', 'zh', 'cn', '5.5.1',
    '{"name_zh":{"lang":"zh","status":"source_original"},"description_zh":{"lang":"zh","status":"source_original"}}',
    'https://www.taptap.cn/moment/870483408487189154'
)
ON CONFLICT(character_key, weapon_key) DO UPDATE SET name_zh=excluded.name_zh,
    description_zh=excluded.description_zh, source_status=excluded.source_status,
    translation_status=excluded.translation_status, source_language=excluded.source_language,
    server_region=excluded.server_region, game_version=excluded.game_version,
    field_provenance=excluded.field_provenance, source_url=excluded.source_url;

INSERT INTO zh_guides(
    character_key, guide_type, content_json, source_status, translation_status,
    source_language, server_region, game_version, field_provenance, source_url
)
VALUES (
    '平野亚兰凛华', 'review',
    '{"review":"【官方机制介绍，非实测评价】平野亚兰·凛华可造成祝福与诅咒双适应性伤害，所有伤害必定造成weak效果并积累弱点伤害系数；同时可将己方队友全部适应性暂时升级为「吸」并赋予护盾。","pros":[],"cons":["具体技能、倍率、意识及养成配队待完整资料补录。"]}',
    'official', 'source_original', 'zh', 'cn', '5.5.1',
    '{"review":{"lang":"zh","status":"source_original"}}',
    'https://www.taptap.cn/moment/870483408487189154'
)
ON CONFLICT(character_key, guide_type) DO UPDATE SET content_json=excluded.content_json,
    source_status=excluded.source_status, translation_status=excluded.translation_status,
    source_language=excluded.source_language, server_region=excluded.server_region,
    game_version=excluded.game_version, field_provenance=excluded.field_provenance,
    source_url=excluded.source_url;

COMMIT;
