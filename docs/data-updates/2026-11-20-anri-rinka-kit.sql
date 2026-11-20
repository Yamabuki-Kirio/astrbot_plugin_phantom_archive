BEGIN IMMEDIATE;

UPDATE source_registry
SET row_count = row_count + 10,
    note = '社区整理的角色/技能/武器/意识数据；含未实装或未来内容时，数值可能调整。'
WHERE url_prefix = 'https://lufel.net';

UPDATE zh_characters
SET description_zh = '五星SP怪盗，初始人格面具为「真蒂莱斯基」。定位反抗，造成祝福与诅咒双属性伤害；双属性伤害视为弱点伤害，并通过「决断」「不凋帷幕」强化输出与队伍生存。LV1基础属性：生命283、攻击92、防御48、速度95。中文CV喵酱，日文CV石见舞菜香。5.5.1版本登场。',
    fetched_at = '2026-11-20T04:00:00Z'
WHERE source_key = '平野亚兰凛华';

DELETE FROM zh_weapons
WHERE character_key = '平野亚兰凛华' AND weapon_key = '千镜的余冕';

INSERT INTO zh_weapons(
    character_key, weapon_key, name_zh, rarity, health, attack, defense,
    skill_name_zh, description_zh, source_status, translation_status,
    source_language, server_region, game_version, field_provenance, source_url
) VALUES (
    '平野亚兰凛华', '千镜的余烬', '千镜的余烬', 5, 2339, 760, 396,
    '千镜的余烬',
    '暴击率提升18.1%。提供护盾后，自身暴击效果提升32.8%，持续4回合；触发弱点效果时，该次弱点伤害提升10%。以上为5星+0数据。',
    'community', 'human_translated', 'en', 'cn', '5.5.1',
    '{"name_zh":{"lang":"zh","status":"source_original"},"description_zh":{"lang":"en","status":"human_translated"}}',
    'https://lufel.net/en/character/anri-linhua/'
)
ON CONFLICT(character_key, weapon_key) DO UPDATE SET
    name_zh=excluded.name_zh, rarity=excluded.rarity, health=excluded.health,
    attack=excluded.attack, defense=excluded.defense,
    skill_name_zh=excluded.skill_name_zh, description_zh=excluded.description_zh,
    source_status=excluded.source_status, translation_status=excluded.translation_status,
    source_language=excluded.source_language, server_region=excluded.server_region,
    game_version=excluded.game_version, field_provenance=excluded.field_provenance,
    source_url=excluded.source_url;

DELETE FROM zh_skills WHERE character_key = '平野亚兰凛华';

INSERT INTO zh_skills(
    character_key, skill_key, name_zh, element_zh, type_zh, description_zh,
    sp, cooldown, source_status, translation_status, source_language,
    server_region, game_version, field_provenance, source_url
) VALUES
('平野亚兰凛华','skill1','圣邪镜华','祝福/诅咒','单体伤害','对1名敌人造成562.6%攻击力的祝福与诅咒双属性伤害；自身暴击效果提升117.1%，持续2回合，并获得1层「决断」。以上为技能等级10数据。',22,0,'community','human_translated','en','cn','5.5.1','{"name_zh":{"lang":"zh","status":"source_original"},"description_zh":{"lang":"en","status":"human_translated"}}','https://lufel.net/en/character/anri-linhua/'),
('平野亚兰凛华','skill2','救赎镜域','祝福/诅咒','群体护盾','使全体同伴获得「不凋帷幕」，自身获得1层「决断」；随后全体同伴的全部适应性变为吸收，吸收比例由20%提升至29.8%，持续2回合。以上为技能等级10数据。',22,1,'community','human_translated','en','cn','5.5.1','{"name_zh":{"lang":"zh","status":"source_original"},"description_zh":{"lang":"en","status":"human_translated"}}','https://lufel.net/en/character/anri-linhua/'),
('平野亚兰凛华','skill3','冥天流光','祝福/诅咒','单体伤害','对1名敌人造成906.5%攻击力的祝福与诅咒双属性伤害。持有「决断」时，本次攻击穿透提升15%、伤害提升150%。以上为技能等级10数据。',26,1,'community','human_translated','en','cn','5.5.1','{"name_zh":{"lang":"zh","status":"source_original"},"description_zh":{"lang":"en","status":"human_translated"}}','https://lufel.net/en/character/anri-linhua/'),
('平野亚兰凛华','skill_highlight','HIGHLIGHT','祝福/诅咒','单体伤害','对1名敌人造成1297.7%攻击力的祝福与诅咒双属性伤害；自身攻击力提升48.8%，持续3回合，随后使全体同伴获得「不凋帷幕」。资料页另标4回合冷却。',NULL,4,'community','human_translated','en','cn','5.5.1','{"name_zh":{"lang":"zh","status":"source_original"},"description_zh":{"lang":"en","status":"human_translated"}}','https://lufel.net/en/character/anri-linhua/'),
('平野亚兰凛华','passive1','祝福强化','被动',NULL,'祝福属性伤害提升36%。',NULL,0,'community','human_translated','en','cn','5.5.1','{"description_zh":{"lang":"en","status":"human_translated"}}','https://lufel.net/en/character/anri-linhua/'),
('平野亚兰凛华','passive2','诅咒强化','被动',NULL,'诅咒属性伤害提升36%。',NULL,0,'community','human_translated','en','cn','5.5.1','{"description_zh":{"lang":"en","status":"human_translated"}}','https://lufel.net/en/character/anri-linhua/'),
('平野亚兰凛华','core','凝望光与暗','被动',NULL,'造成的祝福与诅咒双属性伤害视为弱点伤害。战斗开始时，使全体同伴获得「不凋帷幕」。',NULL,0,'community','human_translated','en','cn','5.5.1','{"name_zh":{"lang":"zh","status":"source_original"},"description_zh":{"lang":"en","status":"human_translated"}}','https://lufel.net/en/character/anri-linhua/');

INSERT INTO zh_guides(
    character_key, guide_type, content_json, source_status, translation_status,
    source_language, server_region, game_version, field_provenance, source_url
) VALUES
('平野亚兰凛华','review','{"review":"平野亚兰凛华是祝福／诅咒双属性反抗输出。双属性伤害视为弱点伤害；开局可为全队添加「不凋帷幕」，技能2还能把全队适应性暂时变为吸收。她通过「决断」强化冥天流光，并兼顾单体爆发与队伍保护。当前资料页仍标记为未来内容，数值可能调整。","pros":["祝福与诅咒双属性伤害视为弱点伤害。","具备高倍率单体爆发与全队吸收保护。"],"cons":["技能数值来自未正式定稿页面，后续版本可能微调。"]}','community','human_translated','en','cn','5.5.1','{"review":{"lang":"en","status":"human_translated"}}','https://lufel.net/en/character/anri-linhua/'),
('平野亚兰凛华','setting','{"role":"祝福／诅咒单体输出","skill_levels":{"skill1":"MAX","skill2":"8","skill3":"MAX!","skill4":"MAX"},"skill_priority":"3>1>4>2","note":["冥天流光优先拉满，其次圣邪镜华与HIGHLIGHT，救赎镜域升至8级即可。"]}','community','human_translated','en','cn','5.5.1','{"skill_priority":{"lang":"en","status":"human_translated"}}','https://lufel.net/en/character/anri-linhua/'),
('平野亚兰凛华','operation','{"note":["1=圣邪镜华，2=救赎镜域，3=冥天流光。"],"basic":[{"group":"r0","variant":"standard","label":"无意识1","turns":["1","2","3","4","5","6"],"skills":["1","3","2","3","1","3"],"row_labels":{"turns":"回合","skills":"技能"}},{"group":"r1","variant":"standard","label":"有意识1","turns":["1","2","3","4","5","6"],"skills":["2+1","3","2+1","3","2+1","3"],"row_labels":{"turns":"回合","skills":"技能"}}]}','community','human_translated','en','cn','5.5.1','{"basic":{"lang":"en","status":"human_translated"}}','https://lufel.net/en/character/anri-linhua/'),
('平野亚兰凛华','ritual','{"name":"平野亚兰凛华","entries":[{"key":"r1","name":"我所描绘的","detail":"救赎镜域不消耗回合与SP；「决断」叠加上限提升至2层。"},{"key":"r2","name":"在一切褪色前","detail":"每层「决断」使暴击效果提升20%，冥天流光伤害提升25%。2层时合计提升40%暴击效果与50%冥天流光伤害，该满层值为计算结果。"},{"key":"r3","name":"于光影中落笔","detail":"救赎镜域、冥天流光技能等级提升3级，至多提升至15级。"},{"key":"r4","name":"爱会跨越时光","detail":"释放HIGHLIGHT后，攻击力额外提升20%，最多叠加2层。"},{"key":"r5","name":"向荒诞处着色","detail":"圣邪镜华、战斗技巧技能等级提升3级，至多提升至15级。"},{"key":"r6","name":"终将显现的理想国","detail":"每层「不凋帷幕」破碎或到期时，暴击效果提升10%，最多8层；8层合计80%为计算结果。HIGHLIGHT可以暴击且穿透提升15%；释放冥天流光后追加1次HIGHLIGHT。"}]}','community','human_translated','en','cn','5.5.1','{"entries":{"lang":"en","status":"human_translated"}}','https://lufel.net/en/character/anri-linhua/')
ON CONFLICT(character_key, guide_type) DO UPDATE SET
    content_json=excluded.content_json, source_status=excluded.source_status,
    translation_status=excluded.translation_status, source_language=excluded.source_language,
    server_region=excluded.server_region, game_version=excluded.game_version,
    field_provenance=excluded.field_provenance, source_url=excluded.source_url;

COMMIT;
