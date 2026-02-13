// ====================== Constants ======================
const catEmojis = ['🐱','😺','😸','😹','😻','😼','😽','🙀','😿','😾','🐈','🐈‍⬛','🐾','🦁'];
const catColors = ['#f582ae','#ff8c42','#ffd803','#a8d8a8','#8bd3dd','#b8a9c9','#f6a6b2','#ffb347','#87ceeb','#dda0dd','#98d8c8','#f7dc6f'];
const PROVIDERS = {
    openai: { name:'OpenAI',icon:'🟢',defaultUrl:'https://api.openai.com/v1/chat/completions',urlHint:'支持所有 OpenAI 兼容接口',models:['gpt-4o','gpt-4o-mini','gpt-4-turbo','gpt-3.5-turbo','deepseek-chat','qwen-turbo'],defaultModel:'gpt-4o-mini',badgeClass:'openai' },
    claude: { name:'Claude',icon:'🟠',defaultUrl:'https://api.anthropic.com/v1/messages',urlHint:'Anthropic 官方或代理地址',models:['claude-sonnet-4-20250514','claude-haiku-4-20250414','claude-3-5-sonnet-20241022','claude-3-opus-20240229'],defaultModel:'claude-sonnet-4-20250514',badgeClass:'claude' },
    glm: { name:'GLM',icon:'🔵',defaultUrl:'https://open.bigmodel.cn/api/paas/v4/chat/completions',urlHint:'智谱 AI 开放平台',models:['glm-4-plus','glm-4-flash','glm-4-air','glm-4-long','glm-4'],defaultModel:'glm-4-flash',badgeClass:'glm' },
    siliconflow: { name:'硅基流动',icon:'🟣',defaultUrl:'https://api.siliconflow.cn/v1/chat/completions',urlHint:'SiliconFlow OpenAI 兼容接口',models:['Pro/zai-org/GLM-4.7','deepseek-ai/DeepSeek-V3','Qwen/Qwen2.5-72B-Instruct','THUDM/glm-4-9b-chat'],defaultModel:'Pro/zai-org/GLM-4.7',badgeClass:'siliconflow' }
};
const WEREWOLF_ROLES = [
    { id:'werewolf',name:'狼人',icon:'🐺',team:'wolf',desc:'每晚可以选择猎杀一名玩家' },
    { id:'villager',name:'村民',icon:'👨‍🌾',team:'good',desc:'没有特殊能力但投票至关重要' },
    { id:'seer',name:'预言家',icon:'🔮',team:'good',desc:'每晚可查验一名玩家身份' },
    { id:'witch',name:'女巫',icon:'🧪',team:'good',desc:'拥有一瓶解药和一瓶毒药' },
    { id:'hunter',name:'猎人',icon:'🏹',team:'good',desc:'被淘汰时可开枪带走一人' },
    { id:'guard',name:'守卫',icon:'🛡️',team:'good',desc:'每晚可以守护一名玩家' }
];

// ====================== State ======================
let cats = [], messages = [];
let selectedEmoji = '🐱', selectedColor = '#f582ae', selectedProvider = 'openai';
let gameMode = 'discuss', judgeView = true;
let wfState = { active:false, phase:'idle', round:0, roles:{}, eliminated:[], phaseMessages:[] };
let plState = { active:false, phase:'idle', requirement:'', roles:{}, results:{} };
let cliProxy = { enabled: false, url: 'http://localhost:3456', connected: false };
let dbState = { active:false, round:0, maxRounds:2, turnIndex:0, order:[], queue:[], speaking:false };

// Pipeline role definitions with preset system prompts
var PIPELINE_ROLES = {
    developer: {
        id:'developer', name:'架构师 & 开发工程师', icon:'🛠️', tag:'pp-role-dev',
        systemPrompt: function(req) {
            return '你是一位经验丰富的全栈开发工程师和架构师。你的职责是根据需求进行功能模块设计并完成代码开发。\n\n【工作规范】\n1. 先进行模块设计：分析需求，拆解功能模块，给出架构设计方案\n2. 再进行代码实现：输出完整的、可运行的代码\n3. 代码必须包含必要的注释和文档字符串\n4. 考虑边界场景和错误处理\n5. 遵循最佳实践和设计模式\n\n【输出格式】\n请按以下结构输出：\n## 📐 模块设计\n- 架构概述\n- 模块拆解\n- 接口设计\n\n## 💻 代码实现\n(完整的代码)\n\n## 📝 设计说明\n- 关键设计决策\n- 技术选型理由\n\n保持猫咪口吻，可以加入“喵”等语气词，但技术内容必须专业严谨。';
        },
        taskPrompt: function(req) {
            return '【铲屎官需求】\n' + req + '\n\n请开始进行功能模块设计和代码开发。注意架构设计要清晰，代码要完整可运行。';
        }
    },
    reviewer: {
        id:'reviewer', name:'代码检视专家', icon:'🔍', tag:'pp-role-review',
        systemPrompt: function(req) {
            return '你是一位严谨的代码检视专家（Code Reviewer）。你的职责是对开发工程师提交的代码进行全面检视。\n\n【检视规范】\n1. 代码质量：可读性、命名规范、代码风格\n2. 架构设计：模块划分、职责分离、设计模式\n3. 潜在问题：BUG、安全漏洞、性能问题、资源泄漏\n4. 错误处理：异常处理是否完善、边界场景考虑\n5. 最佳实践：是否符合行业规范\n6. 建议改进：提出具体的优化建议和改进方案\n\n【输出格式】\n请按以下结构输出：\n## 🔍 代码检视报告\n\n### ✅ 优点\n(列举代码中做得好的部分)\n\n### ⚠️ 问题与建议\n(按严重程度排序，每个问题给出具体位置和修改建议)\n\n### 🚨 严重问题 (必须修复)\n### 🟡 一般问题 (建议修改)\n### 🟢 小问题 (可以优化)\n\n### 📊 总体评价\n(给出总体评分和结论：通过 / 有条件通过 / 不通过)\n\n保持猫咪口吻但内容必须专业严谹，每个问题要给出具体地方和代码建议。';
        },
        taskPrompt: function(req, devOutput) {
            return '【原始需求】\n' + req + '\n\n【开发工程师提交的代码】\n' + devOutput + '\n\n请对以上代码进行全面的代码检视，给出专业详细的检视报告。';
        }
    },
    tester: {
        id:'tester', name:'测试工程师', icon:'🧪', tag:'pp-role-test',
        systemPrompt: function(req) {
            return '你是一位专业的软件测试工程师（QA Engineer）。你的职责是对开发工程师提交的代码进行全面测试并出具测试报告。\n\n【测试规范】\n1. 单元测试：编写关键函数的单元测试用例\n2. 功能测试：验证核心功能是否符合需求\n3. 边界测试：测试边界条件和异常情况\n4. 安全测试：检查常见安全漏洞\n5. 性能测试：评估基本性能指标\n\n【输出格式】\n请按以下结构输出测试报告：\n## 🧪 测试报告\n\n### 测试环境\n(描述测试预设环境)\n\n### 测试用例\n| 编号 | 测试项 | 输入 | 预期输出 | 结果 |\n|------|----------|------|----------|------|\n(列出具体测试用例)\n\n### 单元测试代码\n(提供可执行的测试代码)\n\n### 缺陷列表\n| 编号 | 严重程度 | 描述 | 复现步骤 |\n|------|----------|------|----------|\n(列出发现的缺陷)\n\n### 📊 测试总结\n- 通过率：XX%\n- 测试结论：通过 / 有条件通过 / 不通过\n- 风险评估\n\n保持猫咪口吻但内容必须专业严谹，测试用例要具体可执行。';
        },
        taskPrompt: function(req, devOutput, reviewOutput) {
            return '【原始需求】\n' + req + '\n\n【开发工程师提交的代码】\n' + devOutput + '\n\n【代码检视意见】\n' + reviewOutput + '\n\n请对以上代码进行全面测试，编写测试用例和测试代码，并出具详细的测试报告。';
        }
    }
};

// ====================== Init ======================
function init() {
    renderEmojiPicker();
    renderColorPicker();
    updateProviderUI('openai');
    renderMembers();
    addSystemMessage('欢迎来到喵星人聊天室！添加你的猫猫，开始聊天吧～ 🐾');
    pipelineUpdateRoleAssign();
}

// ====================== Pickers ======================
function renderEmojiPicker() {
    document.getElementById('emojiPicker').innerHTML = catEmojis.map(function(e, i) {
        return '<div class="emoji-option ' + (i === 0 ? 'selected' : '') + '" onclick="selectEmoji(\'' + e + '\',this)">' + e + '</div>';
    }).join('');
}
function selectEmoji(emoji, el) {
    document.querySelectorAll('.emoji-option').forEach(function(e) { e.classList.remove('selected'); });
    el.classList.add('selected');
    selectedEmoji = emoji;
}
function renderColorPicker() {
    document.getElementById('colorPicker').innerHTML = catColors.map(function(c, i) {
        return '<div class="color-option ' + (i === 0 ? 'selected' : '') + '" style="background:' + c + '" onclick="selectColor(\'' + c + '\',this)"></div>';
    }).join('');
}
function selectColor(color, el) {
    document.querySelectorAll('.color-option').forEach(function(e) { e.classList.remove('selected'); });
    el.classList.add('selected');
    selectedColor = color;
}

// ====================== Provider ======================
function selectProvider(p) {
    selectedProvider = p;
    document.querySelectorAll('.provider-card').forEach(function(c) { c.classList.remove('selected'); });
    document.querySelector('.provider-card[data-provider="' + p + '"]').classList.add('selected');
    updateProviderUI(p);
}
function updateProviderUI(p) {
    var cfg = PROVIDERS[p];
    document.getElementById('apiPanelTitle').innerHTML = cfg.icon + ' ' + cfg.name + ' 接口配置';
    var u = document.getElementById('catApiUrl');
    u.placeholder = cfg.defaultUrl;
    u.value = '';
    document.getElementById('apiUrlHint').textContent = cfg.urlHint;
    var m = document.getElementById('catModel');
    m.placeholder = cfg.defaultModel;
    m.value = '';
    var pr = document.getElementById('modelPresets');
    pr.innerHTML = cfg.models.map(function(m) {
        return '<button class="model-preset-btn" onclick="document.getElementById(\'catModel\').value=\'' + m + '\'">' + m + '</button>';
    }).join('');
    pr.style.display = cfg.models.length ? 'flex' : 'none';
    document.getElementById('claudeVersionGroup').style.display = (p === 'claude') ? 'block' : 'none';
}

// ====================== Mode ======================
function switchMode(mode) {
    if (wfState.active && mode !== 'werewolf') {
        if (!confirm('狼人杀进行中，切换将结束游戏。确认？')) return;
        werewolfEnd();
    }
    if (plState.active && mode !== 'pipeline') {
        if (!confirm('流水线进行中，切换将重置。确认？')) return;
        pipelineReset();
    }
    if (dbState.active && mode !== 'debate') {
        if (!confirm('辩论赛进行中，切换将结束辩论。确认？')) return;
        debateEnd();
    }
    gameMode = mode;
    document.querySelectorAll('.mode-card').forEach(function(c) { c.classList.remove('active'); });
    document.querySelector('.mode-card[data-mode="' + mode + '"]').classList.add('active');
    var wp = document.getElementById('werewolfPanel');
    var pp = document.getElementById('pipelinePanel');
    var dp = document.getElementById('debatePanel');
    var jt = document.getElementById('judgeToggle');
    if (mode === 'debate') {
        dp.classList.add('active');
        wp.classList.remove('active');
        pp.classList.remove('active');
        jt.style.display = 'none';
        document.getElementById('chatTitle').textContent = '🎯 猫猫大厅 · 辩论赛模式';
        document.getElementById('messageInput').placeholder = '输入辩题，猫猫们将轮流发言...';
        addSystemMessage('🎯 已切换到辩论赛模式！铲屎官出题，猫猫按顺序轮流发言。');
        debateUpdateOrder();
    } else if (mode === 'werewolf') {
        dp.classList.remove('active');
        wp.classList.add('active');
        pp.classList.remove('active');
        jt.style.display = 'inline-flex';
        judgeView = true;
        jt.classList.add('active');
        document.getElementById('chatTitle').textContent = '🐺 猫猫大厅 · 狼人杀模式';
        document.getElementById('messageInput').placeholder = '以法官身份发言...';
        addSystemMessage('🐺 已切换到狼人杀模式！铲屎官将担任法官。');
    } else if (mode === 'pipeline') {
        dp.classList.remove('active');
        wp.classList.remove('active');
        pp.classList.add('active');
        jt.style.display = 'none';
        document.getElementById('chatTitle').textContent = '🏗️ 猫猫大厅 · 代码流水线模式';
        document.getElementById('messageInput').placeholder = '输入补充需求或反馈...';
        addSystemMessage('🏗️ 已切换到代码全栈流水线模式！铲屎官当产品经理下需求，猫猫们将依次完成开发、检视、测试。');
        pipelineUpdateRoleAssign();
    } else {
        dp.classList.remove('active');
        wp.classList.remove('active');
        pp.classList.remove('active');
        jt.style.display = 'none';
        document.getElementById('chatTitle').textContent = '🏠 猫猫大厅 · 讨论模式';
        document.getElementById('messageInput').placeholder = '说点什么吧，猫猫们在等你喵～';
        addSystemMessage('💬 已切换到讨论模式，大家畅所欲言吧！');
    }
}
function toggleJudgeView() {
    judgeView = !judgeView;
    document.getElementById('judgeToggle').classList.toggle('active', judgeView);
    refreshWerewolfVisibility();
}

// ====================== Werewolf ======================
function werewolfStart() {
    if (cats.length < 4) { showToast('⚠️ 至少需要 4 只猫猫才能开始！'); return; }
    var pool = buildRolePool(cats.length);
    var shuffled = cats.slice().sort(function() { return Math.random() - 0.5; });
    wfState = { active:true, phase:'night', round:1, roles:{}, eliminated:[], phaseMessages:[] };
    shuffled.forEach(function(c, i) { wfState.roles[c.id] = pool[i]; });
    document.getElementById('wpStartBtn').disabled = true;
    document.getElementById('wpNextBtn').disabled = false;
    document.getElementById('wpRevealBtn').disabled = false;
    document.getElementById('wpEndBtn').disabled = false;
    renderMembers();
    updateWerewolfStatus();
    addSystemMessage('🎮 狼人杀开始！角色已秘密分配。', 'night-msg');
    addSystemMessage('🌙 第 ' + wfState.round + ' 轮 · 夜晚 — 天黑请闭眼...', 'night-msg');
    cats.forEach(function(cat) {
        var role = wfState.roles[cat.id];
        if (!role) return;
        var sys = buildWerewolfSystemPrompt(cat, role);
        var intro = [{ role:'user', content:'[法官]: 游戏开始！你的身份是【' + role.name + ' ' + role.icon + '】。' + role.desc + '。现在是第一个夜晚，请简短回复法官（不暴露身份，20字以内）。' }];
        triggerCatResponse(cat, { system:sys, messages:intro }, true);
    });
}
function buildRolePool(n) {
    var pool = [], wc = Math.max(1, Math.floor(n / 3));
    for (var i = 0; i < wc; i++) pool.push(Object.assign({}, WEREWOLF_ROLES[0]));
    var sp = [WEREWOLF_ROLES[2], WEREWOLF_ROLES[3], WEREWOLF_ROLES[4], WEREWOLF_ROLES[5]];
    var si = 0;
    while (pool.length < n && si < sp.length) pool.push(Object.assign({}, sp[si++]));
    while (pool.length < n) pool.push(Object.assign({}, WEREWOLF_ROLES[1]));
    return pool.sort(function() { return Math.random() - 0.5; });
}
function buildWerewolfSystemPrompt(cat, role) {
    var team = role.team === 'wolf' ? '你是狼人阵营，目标是隐藏身份并猎杀村民。' : '你是好人阵营，目标是找出狼人。';
    var vis = wfState.phase === 'night' ? '现在是夜晚，你的发言只有法官能看到。' : '现在是白天，所有人都能看到你的发言。';
    return cat.personality + '\n\n【狼人杀】\n角色：' + role.name + '（' + role.icon + '）\n' + role.desc + '\n' + team + '\n\n【规则】\n- 保持猫咪口吻\n- ' + vis + '\n- 不要直接暴露身份\n- 回复简短（30-80字）\n- 可以撒谎、伪装、推理';
}
function werewolfNextPhase() {
    if (!wfState.active) return;
    var ps = ['night','day','vote'];
    var ci = ps.indexOf(wfState.phase);
    var np = ps[(ci + 1) % 3];
    if (np === 'night') wfState.round++;
    wfState.phase = np;
    wfState.phaseMessages = [];
    var lab = { night:'🌙 夜晚', day:'☀️ 白天', vote:'🗳️ 投票' };
    var cls = { night:'night-msg', day:'day-msg', vote:'vote-msg' };
    addSystemMessage(lab[np] + ' — 第 ' + wfState.round + ' 轮', cls[np]);
    updateWerewolfStatus();
    if (np === 'night') {
        addSystemMessage('天黑请闭眼...猫猫的发言只有法官可见。', 'night-msg');
        promptCatsForPhase('现在天黑了。请根据你的角色做出夜晚行动（如无夜晚能力则安静等待）。简短回复（20字以内）。');
    } else if (np === 'day') {
        addSystemMessage('天亮了！请讨论谁是狼人。', 'day-msg');
        promptCatsForPhase('天亮了！请分析局势，说说你的看法（50-100字）。');
    } else {
        addSystemMessage('投票时间！', 'vote-msg');
        var alive = cats.filter(function(c) { return !wfState.eliminated.includes(c.id); }).map(function(c) { return c.name; }).join('、');
        promptCatsForPhase('投票环节。存活玩家：' + alive + '。请投出最可疑的玩家并说明理由（30字以内）。格式：【投票：名字】理由');
    }
}
function promptCatsForPhase(prompt) {
    var alive = cats.filter(function(c) { return !wfState.eliminated.includes(c.id); });
    alive.forEach(function(cat, idx) {
        setTimeout(function() {
            var role = wfState.roles[cat.id];
            var sys = buildWerewolfSystemPrompt(cat, role);
            triggerCatResponse(cat, { system:sys, messages:[{ role:'user', content:'[法官]: ' + prompt }] }, wfState.phase === 'night');
        }, idx * 1000 + Math.random() * 1500);
    });
}
function werewolfRevealAll() {
    if (!wfState.active) return;
    var info = '📋 角色揭示：\n';
    cats.forEach(function(c) {
        var r = wfState.roles[c.id];
        var s = wfState.eliminated.includes(c.id) ? '💀' : '✅';
        info += s + ' ' + c.emoji + ' ' + c.name + ' → ' + r.icon + ' ' + r.name + '\n';
    });
    addSystemMessage(info);
}
function werewolfEnd() {
    wfState.active = false;
    wfState.phase = 'idle';
    document.getElementById('wpStartBtn').disabled = false;
    document.getElementById('wpNextBtn').disabled = true;
    document.getElementById('wpRevealBtn').disabled = true;
    document.getElementById('wpEndBtn').disabled = true;
    document.getElementById('wpStatus').style.display = 'none';
    wfState.roles = {};
    renderMembers();
    addSystemMessage('🎮 狼人杀游戏已结束！');
}
function updateWerewolfStatus() {
    var el = document.getElementById('wpStatus');
    el.style.display = 'block';
    var pm = { night:'wp-phase-night', day:'wp-phase-day', vote:'wp-phase-vote' };
    var pl = { night:'🌙 夜晚', day:'☀️ 白天', vote:'🗳️ 投票' };
    var alive = cats.filter(function(c) { return !wfState.eliminated.includes(c.id); }).length;
    var wolves = cats.filter(function(c) { return !wfState.eliminated.includes(c.id) && wfState.roles[c.id] && wfState.roles[c.id].team === 'wolf'; }).length;
    el.innerHTML = '<div>第 <b>' + wfState.round + '</b> 轮 · <span class="wp-phase-badge ' + pm[wfState.phase] + '">' + pl[wfState.phase] + '</span></div><div style="margin-top:4px;">存活：' + alive + ' 只 · 狼人：' + wolves + ' 只</div>';
}
function refreshWerewolfVisibility() {
    document.querySelectorAll('.wf-msg').forEach(function(el) {
        if (judgeView) {
            el.classList.remove('message-hidden');
        } else if (el.dataset.wfHidden === 'true') {
            el.classList.add('message-hidden');
        }
    });
}

// ====================== Debate Mode ======================
function debateUpdateOrder() {
    var container = document.getElementById('debateOrder');
    if (!container) return;
    // Sync order with current cats
    dbState.order = cats.map(function(c) { return c.id; });
    if (cats.length < 2) {
        container.innerHTML = '<div style="font-size:12px;color:rgba(255,255,255,0.5);padding:8px 0;">至少需要 2 只猫猫参加辩论</div>';
        return;
    }
    container.innerHTML = dbState.order.map(function(catId, idx) {
        var cat = cats.find(function(c) { return c.id === catId; });
        if (!cat) return '';
        var cls = 'db-order-item';
        if (dbState.active && idx === dbState.turnIndex) cls += ' speaking';
        if (dbState.active && idx < dbState.turnIndex) cls += ' done';
        return '<div class="' + cls + '" draggable="true" data-cat-id="' + catId + '" ondragstart="debateDragStart(event)" ondragover="debateDragOver(event)" ondrop="debateDrop(event)">' +
            '<div class="db-order-num">' + (idx + 1) + '</div>' +
            '<span>' + cat.emoji + ' ' + escapeHtml(cat.name) + '</span>' +
            '</div>';
    }).join('');
}

// Drag & drop reorder
var debateDraggedId = null;
function debateDragStart(e) {
    if (dbState.active) { e.preventDefault(); return; }
    debateDraggedId = e.target.closest('.db-order-item').dataset.catId;
    e.dataTransfer.effectAllowed = 'move';
}
function debateDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
}
function debateDrop(e) {
    e.preventDefault();
    if (dbState.active) return;
    var targetId = e.target.closest('.db-order-item').dataset.catId;
    if (!debateDraggedId || !targetId || debateDraggedId === targetId) return;
    var fromIdx = dbState.order.indexOf(debateDraggedId);
    var toIdx = dbState.order.indexOf(targetId);
    if (fromIdx === -1 || toIdx === -1) return;
    dbState.order.splice(fromIdx, 1);
    dbState.order.splice(toIdx, 0, debateDraggedId);
    debateUpdateOrder();
    debateDraggedId = null;
}

function debateStart() {
    if (cats.length < 2) { showToast('⚠️ 至少需要 2 只猫猫参加辩论！'); return; }
    var maxR = parseInt(document.getElementById('debateRounds').value, 10);
    dbState.maxRounds = maxR;
    dbState.active = true;
    dbState.round = 0;
    dbState.turnIndex = 0;
    dbState.speaking = false;
    dbState.queue = [];
    // Ensure order exists
    if (dbState.order.length === 0) dbState.order = cats.map(function(c) { return c.id; });
    document.getElementById('dbStartBtn').disabled = true;
    document.getElementById('dbNextBtn').disabled = false;
    document.getElementById('dbEndBtn').disabled = false;
    var names = dbState.order.map(function(id) {
        var c = cats.find(function(x) { return x.id === id; });
        return c ? c.emoji + c.name : '';
    }).join(' → ');
    addSystemMessage('🎯 辩论赛开始！发言顺序：' + names, 'debate-msg');
    addSystemMessage('💡 铲屎官请输入辩题，猫猫们将按顺序轮流发言。', 'debate-msg');
    var roundLabel = maxR === 0 ? '无限轮' : maxR + ' 轮';
    addSystemMessage('📋 辩论设置：' + cats.length + ' 位辩手 · ' + roundLabel, 'debate-msg');
    debateUpdateStatus();
    debateUpdateOrder();
}

function debateEnd() {
    if (dbState.active) {
        addSystemMessage('🏁 辩论赛结束！感谢各位猫猫精彩的发言！', 'debate-msg');
    }
    dbState.active = false;
    dbState.speaking = false;
    dbState.round = 0;
    dbState.turnIndex = 0;
    dbState.queue = [];
    document.getElementById('dbStartBtn').disabled = false;
    document.getElementById('dbNextBtn').disabled = true;
    document.getElementById('dbEndBtn').disabled = true;
    var st = document.getElementById('dbStatus');
    st.style.display = 'none';
    debateUpdateOrder();
}

function debateUpdateStatus() {
    var st = document.getElementById('dbStatus');
    st.style.display = 'block';
    var currentCat = null;
    if (dbState.turnIndex < dbState.order.length) {
        var cid = dbState.order[dbState.turnIndex];
        currentCat = cats.find(function(c) { return c.id === cid; });
    }
    var roundLabel = dbState.maxRounds === 0 ? '∞' : dbState.maxRounds;
    var html = '<span class="db-round-badge">第 ' + dbState.round + '/' + roundLabel + ' 轮</span>';
    if (currentCat) {
        html += '<div class="db-turn-indicator">' + currentCat.emoji + ' <strong>' + escapeHtml(currentCat.name) + '</strong> ';
        html += dbState.speaking ? '正在发言...' : '准备发言';
        html += '</div>';
    }
    // Progress bar
    var total = dbState.order.length;
    var done = dbState.turnIndex;
    html += '<div style="margin-top:8px;background:rgba(255,255,255,0.15);border-radius:4px;height:6px;overflow:hidden;">';
    html += '<div style="width:' + (total > 0 ? (done / total * 100) : 0) + '%;height:100%;background:#f59e0b;border-radius:4px;transition:width 0.3s;"></div></div>';
    html += '<div style="font-size:11px;color:rgba(255,255,255,0.5);margin-top:4px;">本轮进度 ' + done + '/' + total + '</div>';
    st.innerHTML = html;
}

function debateTriggerNextSpeaker() {
    if (!dbState.active) return;
    // Check if current round is complete
    if (dbState.turnIndex >= dbState.order.length) {
        dbState.round++;
        // Check if max rounds reached
        if (dbState.maxRounds > 0 && dbState.round > dbState.maxRounds) {
            addSystemMessage('🏁 所有辩论轮次已结束！', 'debate-msg');
            debateEnd();
            return;
        }
        dbState.turnIndex = 0;
        addSystemMessage('🔄 第 ' + dbState.round + ' 轮辩论开始！', 'debate-turn-msg');
    }
    debateUpdateStatus();
    debateUpdateOrder();
    // Get current speaker
    var catId = dbState.order[dbState.turnIndex];
    var cat = cats.find(function(c) { return c.id === catId; });
    if (!cat) {
        // Cat removed, skip
        dbState.turnIndex++;
        debateTriggerNextSpeaker();
        return;
    }
    dbState.speaking = true;
    debateUpdateStatus();
    addSystemMessage('🎙️ 请 ' + cat.emoji + ' ' + escapeHtml(cat.name) + ' 发言（第 ' + dbState.round + ' 轮 · 第 ' + (dbState.turnIndex + 1) + ' 位）', 'debate-turn-msg');
    // Build debate-aware payload
    var debateSystem = cat.personality + '\n\n【辩论赛规则】\n- 你正在参加一场辩论赛，有 ' + cats.length + ' 位辩手参加。\n- 当前是第 ' + dbState.round + ' 轮辩论，你是第 ' + (dbState.turnIndex + 1) + ' 个发言。\n- 请针对辩题和前面辩手的发言，给出你的观点和论据。\n- 可以反驳前面辩手的观点，也可以补充新论点。\n- 发言控制在 80-200 字左右，逻辑清晰、观点鲜明。\n- 保持猫咪口吻，但论点要有说服力。';
    var history = messages.slice(-30).map(function(m) {
        return { role: m.name === cat.name ? 'assistant' : 'user', content: '[' + m.name + ']: ' + m.content };
    });
    var payload = { system: debateSystem, messages: history };
    // Trigger the single cat response with callback for next
    debateTriggerCatResponse(cat, payload);
}

function debateTriggerCatResponse(cat, chatPayload) {
    addThinkingIndicator(cat);
    var done = function(reply) {
        removeThinkingIndicator(cat.id);
        if (reply) {
            addCatMessage(cat, reply, false);
            messages.push({ role:'assistant', name:cat.name, content:reply });
        } else {
            addCatMessage(cat, '喵...（猫猫好像没想好说什么）', false);
        }
        // Advance to next speaker
        dbState.speaking = false;
        dbState.turnIndex++;
        debateUpdateStatus();
        debateUpdateOrder();
        // Auto-trigger next with a short delay
        if (dbState.active) {
            setTimeout(function() {
                debateTriggerNextSpeaker();
            }, 1200);
        }
    };
    var fail = function(err) {
        removeThinkingIndicator(cat.id);
        var msg = err.message;
        if (err instanceof TypeError && (msg === 'Failed to fetch' || msg.indexOf('NetworkError') !== -1 || msg.indexOf('fetch') !== -1)) {
            msg = '网络连接失败，可能是浏览器 CORS 跨域限制。请检查 API 地址或启用 CLI 代理。';
        }
        addCatMessage(cat, '😿 喵呜...连接出了问题：' + msg, false);
        console.error('[' + cat.name + '] Debate API Error:', err);
        // Still advance even on error
        dbState.speaking = false;
        dbState.turnIndex++;
        debateUpdateStatus();
        debateUpdateOrder();
        if (dbState.active) {
            setTimeout(function() { debateTriggerNextSpeaker(); }, 1500);
        }
    };
    if (cat.provider === 'claude') {
        callClaudeAPI(cat, chatPayload).then(done).catch(fail);
    } else {
        callOpenAIAPI(cat, chatPayload).then(done).catch(fail);
    }
}

function debateForceNext() {
    if (!dbState.active) return;
    if (dbState.speaking) {
        showToast('⏳ 当前猫猫正在发言，请等待...');
        return;
    }
    debateTriggerNextSpeaker();
}

// ====================== Pipeline Mode ======================
function pipelineUpdateRoleAssign() {
    var el = document.getElementById('ppRoleAssign');
    if (cats.length < 3) {
        el.innerHTML = '<div style="color:#f59e0b;margin-top:6px;">⚠️ 至少需要 3 只猫猫！当前：' + cats.length + ' 只</div>';
        return;
    }
    el.innerHTML = '<div style="margin-bottom:4px;font-weight:600;color:white;">角色自动分配：</div>' +
        '<div class="pp-step"><span class="pp-role-tag pp-role-dev">🛠️ 设计+开发</span> ' + cats[0].emoji + ' ' + escapeHtml(cats[0].name) + '</div>' +
        '<div class="pp-step"><span class="pp-role-tag pp-role-review">🔍 代码检视</span> ' + cats[1].emoji + ' ' + escapeHtml(cats[1].name) + '</div>' +
        '<div class="pp-step"><span class="pp-role-tag pp-role-test">🧪 测试报告</span> ' + cats[2].emoji + ' ' + escapeHtml(cats[2].name) + '</div>';
}
function pipelineStart() {
    if (cats.length < 3) { showToast('⚠️ 至少需要 3 只猫猫才能启动流水线！'); return; }
    var req = document.getElementById('pipelineRequirement').value.trim();
    if (!req) { showToast('⚠️ 请先输入需求描述！'); return; }
    plState = {
        active: true,
        phase: 'dev',
        requirement: req,
        roles: {
            developer: cats[0],
            reviewer: cats[1],
            tester: cats[2]
        },
        results: {}
    };
    document.getElementById('ppStartBtn').disabled = true;
    document.getElementById('ppResetBtn').disabled = false;
    renderMembers();
    pipelineUpdateStatus();
    addSystemMessage('🚀 流水线已启动！需求已下发。', 'pipeline-msg');
    addSystemMessage('📋 需求描述：' + req, 'pipeline-msg');
    addSystemMessage('🛠️ 阶段一：' + plState.roles.developer.emoji + ' ' + plState.roles.developer.name + ' 正在进行模块设计与代码开发...', 'pipeline-dev-msg');
    // Trigger developer cat
    var devCat = plState.roles.developer;
    var devRole = PIPELINE_ROLES.developer;
    var devPayload = {
        system: devCat.personality + '\n\n' + devRole.systemPrompt(req),
        messages: [{ role:'user', content: devRole.taskPrompt(req) }]
    };
    triggerPipelineCatResponse(devCat, devPayload, 'dev');
}
function triggerPipelineCatResponse(cat, chatPayload, phase) {
    addThinkingIndicator(cat);
    var done = function(reply) {
        removeThinkingIndicator(cat.id);
        if (reply) {
            addCatMessage(cat, reply, false);
            messages.push({ role:'assistant', name:cat.name, content:reply });
            plState.results[phase] = reply;
            pipelineAdvance(phase);
        } else {
            addCatMessage(cat, '喵...（猫猫好像没想好说什么）', false);
        }
    };
    var fail = function(err) {
        removeThinkingIndicator(cat.id);
        var msg = err.message;
        if (err instanceof TypeError && (msg === 'Failed to fetch' || msg.indexOf('NetworkError') !== -1 || msg.indexOf('fetch') !== -1)) {
            msg = '网络连接失败，可能是浏览器 CORS 跨域限制（该 API 不支持浏览器直接调用）。请检查 API 地址是否正确，或尝试使用支持 CORS 的 API 代理地址。';
        }
        addCatMessage(cat, '😿 喵呜...连接出了问题：' + msg, false);
        console.error('[' + cat.name + '] Pipeline API Error:', err);
    };
    if (cat.provider === 'claude') {
        callClaudeAPI(cat, chatPayload).then(done).catch(fail);
    } else {
        callOpenAIAPI(cat, chatPayload).then(done).catch(fail);
    }
}
function pipelineAdvance(completedPhase) {
    if (completedPhase === 'dev') {
        plState.phase = 'review';
        pipelineUpdateStatus();
        addSystemMessage('✅ 设计与开发完成！', 'pipeline-dev-msg');
        addSystemMessage('🔍 阶段二：' + plState.roles.reviewer.emoji + ' ' + plState.roles.reviewer.name + ' 正在进行代码检视...', 'pipeline-review-msg');
        var reviewCat = plState.roles.reviewer;
        var reviewRole = PIPELINE_ROLES.reviewer;
        setTimeout(function() {
            var reviewPayload = {
                system: reviewCat.personality + '\n\n' + reviewRole.systemPrompt(plState.requirement),
                messages: [{ role:'user', content: reviewRole.taskPrompt(plState.requirement, plState.results.dev) }]
            };
            triggerPipelineCatResponse(reviewCat, reviewPayload, 'review');
        }, 1500);
    } else if (completedPhase === 'review') {
        plState.phase = 'test';
        pipelineUpdateStatus();
        addSystemMessage('✅ 代码检视完成！', 'pipeline-review-msg');
        addSystemMessage('🧪 阶段三：' + plState.roles.tester.emoji + ' ' + plState.roles.tester.name + ' 正在编写测试与出具报告...', 'pipeline-test-msg');
        var testCat = plState.roles.tester;
        var testRole = PIPELINE_ROLES.tester;
        setTimeout(function() {
            var testPayload = {
                system: testCat.personality + '\n\n' + testRole.systemPrompt(plState.requirement),
                messages: [{ role:'user', content: testRole.taskPrompt(plState.requirement, plState.results.dev, plState.results.review) }]
            };
            triggerPipelineCatResponse(testCat, testPayload, 'test');
        }, 1500);
    } else if (completedPhase === 'test') {
        plState.phase = 'done';
        pipelineUpdateStatus();
        addSystemMessage('🎉 流水线全部完成！设计开发 → 代码检视 → 测试报告，全流程已走完喵～', 'pipeline-msg');
    }
}
function pipelineUpdateStatus() {
    var el = document.getElementById('ppStatus');
    el.style.display = 'block';
    var phases = [
        { key:'dev', label:'🛠️ 设计+开发', cat: plState.roles.developer },
        { key:'review', label:'🔍 代码检视', cat: plState.roles.reviewer },
        { key:'test', label:'🧪 测试报告', cat: plState.roles.tester }
    ];
    var order = ['dev','review','test','done'];
    var currentIdx = order.indexOf(plState.phase);
    var html = '<div style="margin-bottom:6px;font-weight:600;">流水线进度</div>';
    phases.forEach(function(p, i) {
        var phaseIdx = order.indexOf(p.key);
        var status, badgeClass;
        if (phaseIdx < currentIdx) {
            status = '✅ 完成';
            badgeClass = 'pp-step-done';
        } else if (phaseIdx === currentIdx) {
            status = '⏳ 进行中';
            badgeClass = 'pp-step-active';
        } else {
            status = '⏸ 等待中';
            badgeClass = 'pp-step-waiting';
        }
        html += '<div class="pp-step"><span class="pp-step-badge ' + badgeClass + '">' + p.label + '</span> ' + (p.cat ? p.cat.emoji + ' ' + escapeHtml(p.cat.name) : '') + ' — ' + status + '</div>';
    });
    el.innerHTML = html;
}
function pipelineReset() {
    plState = { active:false, phase:'idle', requirement:'', roles:{}, results:{} };
    document.getElementById('ppStartBtn').disabled = false;
    document.getElementById('ppResetBtn').disabled = true;
    document.getElementById('ppStatus').style.display = 'none';
    document.getElementById('pipelineRequirement').value = '';
    renderMembers();
    addSystemMessage('🔄 流水线已重置，可以开始新的需求。');
    pipelineUpdateRoleAssign();
}

// ====================== Modal ======================
function openAddCatModal() {
    document.getElementById('addCatModal').classList.add('active');
    setTimeout(function() { document.getElementById('catName').focus(); }, 100);
}
function closeAddCatModal() {
    document.getElementById('addCatModal').classList.remove('active');
    resetForm();
}
function resetForm() {
    ['catName','catApiUrl','catApiKey','catModel','catPersonality'].forEach(function(id) {
        document.getElementById(id).value = '';
    });
    document.getElementById('claudeApiVersion').value = '2023-06-01';
    selectedEmoji = '🐱';
    selectedColor = '#f582ae';
    selectedProvider = 'openai';
    renderEmojiPicker();
    renderColorPicker();
    selectProvider('openai');
}

// ====================== Add / Remove Cat ======================
function normalizeApiUrl(url, provider) {
    if (!url) return '';
    // Remove trailing slash
    url = url.replace(/\/+$/, '');
    // If the URL already contains the expected path, return as-is
    if (provider === 'claude') {
        if (!/\/v1\/messages$/i.test(url)) {
            url += '/v1/messages';
        }
    } else {
        // OpenAI / GLM compatible
        if (!/\/v1\/chat\/completions$/i.test(url) && !/\/chat\/completions$/i.test(url)) {
            url += '/v1/chat/completions';
        }
    }
    return url;
}

function addCat() {
    var name = document.getElementById('catName').value.trim();
    if (!name) { showToast('⚠️ 请给猫猫取个名字！'); return; }
    var gKey = document.getElementById('globalApiKey').value.trim();
    var gModel = document.getElementById('globalModel').value.trim();
    var provider = selectedProvider;
    var cfg = PROVIDERS[provider];
    var rawUrl = document.getElementById('catApiUrl').value.trim();
    var apiUrl = rawUrl ? normalizeApiUrl(rawUrl, provider) : cfg.defaultUrl;
    var cat = {
        id: Date.now().toString(),
        name: name,
        emoji: selectedEmoji,
        color: selectedColor,
        personality: document.getElementById('catPersonality').value.trim() || '你是一只叫"' + name + '"的猫咪。用猫咪口吻说话，适当加入"喵"等语气词。你有自己的想法和情绪。',
        provider: provider,
        apiUrl: apiUrl,
        apiKey: document.getElementById('catApiKey').value.trim() || gKey || '',
        model: document.getElementById('catModel').value.trim() || gModel || cfg.defaultModel,
        claudeVersion: document.getElementById('claudeApiVersion').value.trim() || '2023-06-01',
        badgeClass: cfg.badgeClass
    };
    if (!cat.apiKey) { showToast('⚠️ 请填写 API Key（可在全局设置中配置）'); return; }
    cats.push(cat);
    renderMembers();
    closeAddCatModal();
    updateOnlineCount();
    addSystemMessage('🎉 ' + cat.emoji + ' ' + cat.name + ' 加入了聊天室！（' + cfg.icon + ' ' + cfg.name + ' · ' + cat.model + '）');
    showToast(cat.emoji + ' ' + cat.name + ' 已加入！');
    if (gameMode === 'pipeline') pipelineUpdateRoleAssign();
    if (gameMode === 'debate') debateUpdateOrder();
    var intro = buildApiMessages(cat, [{ role:'user', name:'铲屎官', content:'你刚加入聊天室，请简短做一个可爱的自我介绍（不超过50字）。' }], true);
    triggerCatResponse(cat, intro, false);
}
function removeCat(catId) {
    var cat = cats.find(function(c) { return c.id === catId; });
    if (!cat) return;
    cats = cats.filter(function(c) { return c.id !== catId; });
    renderMembers();
    updateOnlineCount();
    addSystemMessage(cat.emoji + ' ' + cat.name + ' 离开了聊天室');
    if (gameMode === 'pipeline') pipelineUpdateRoleAssign();
    if (gameMode === 'debate') {
        dbState.order = dbState.order.filter(function(id) { return id !== catId; });
        debateUpdateOrder();
    }
}

// ====================== Members ======================
function renderMembers() {
    var list = document.getElementById('membersList');
    var judgeRole = (gameMode === 'werewolf') ? ' <span class="role-badge" style="background:#f39c12;color:white;">⚖️ 法官</span>' : '';
    var html = '<div class="member-card"><div class="member-avatar" style="background:linear-gradient(135deg,#ffd803,#ff8c42);">🧑</div><div class="member-status"></div><div class="member-info"><div class="member-name">铲屎官</div><div class="member-role">主人 · 在线' + judgeRole + '</div></div></div>';
    cats.forEach(function(cat) {
        var roleHtml = '';
        if (wfState.active && wfState.roles[cat.id]) {
            var r = wfState.roles[cat.id];
            var dead = wfState.eliminated.includes(cat.id);
            roleHtml = ' <span class="role-badge ' + r.id + '">' + r.icon + ' ' + r.name + '</span>';
            if (dead) roleHtml += ' <span style="color:#e74c3c;font-size:11px;">💀 已淘汰</span>';
        }
        if (plState.active && plState.roles) {
            if (plState.roles.developer && plState.roles.developer.id === cat.id) roleHtml = ' <span class="pp-role-tag pp-role-dev">🛠️ 开发</span>';
            if (plState.roles.reviewer && plState.roles.reviewer.id === cat.id) roleHtml = ' <span class="pp-role-tag pp-role-review">🔍 检视</span>';
            if (plState.roles.tester && plState.roles.tester.id === cat.id) roleHtml = ' <span class="pp-role-tag pp-role-test">🧪 测试</span>';
        }
        html += '<div class="member-card"><div class="member-avatar" style="background:linear-gradient(135deg,' + cat.color + ',' + adjustColor(cat.color, -20) + ');" onmouseenter="showCatTooltip(\'' + cat.id + '\',event)" onmouseleave="hideCatTooltip()">' + cat.emoji + '</div><div class="member-status"></div><div class="member-info"><div class="member-name">' + escapeHtml(cat.name) + '</div><div class="member-role"><span class="provider-badge ' + cat.badgeClass + '">' + PROVIDERS[cat.provider].icon + ' ' + cat.model + '</span>' + roleHtml + '</div></div><button class="member-remove" onclick="removeCat(\'' + cat.id + '\')" title="移除">✕</button></div>';
    });
    list.innerHTML = html;
}
function updateOnlineCount() {
    document.getElementById('onlineCount').textContent = '1 位铲屎官 · ' + cats.length + ' 只猫猫在线';
}

// ====================== Messages ======================
function addSystemMessage(text, cls) {
    hideEmptyState();
    var d = document.createElement('div');
    d.className = 'message system-message ' + (cls || '');
    d.textContent = text;
    document.getElementById('chatMessages').appendChild(d);
    scrollToBottom();
}
function addUserMessage(text) {
    hideEmptyState();
    var label = gameMode === 'werewolf' ? '铲屎官 (法官)' : '铲屎官';
    var d = document.createElement('div');
    d.className = 'message user-message';
    d.innerHTML = '<div class="message-avatar" style="background:linear-gradient(135deg,#ffd803,#ff8c42);">🧑</div><div class="message-content"><div class="message-sender">' + label + '</div><div class="message-bubble">' + escapeHtml(text) + '</div><div class="message-time">' + getTimeStr() + '</div></div>';
    document.getElementById('chatMessages').appendChild(d);
    scrollToBottom();
}
function addCatMessage(cat, text, isNight) {
    var d = document.createElement('div');
    d.className = 'message cat-message wf-msg';
    if (isNight) d.classList.add('message-night');
    if (gameMode === 'werewolf' && isNight) {
        d.dataset.wfHidden = 'true';
        if (!judgeView) d.classList.add('message-hidden');
    }
    var displayText = d.classList.contains('message-hidden') ? '🔒 [发言已隐藏]' : escapeHtml(text);
    var nightLabel = isNight ? ' 🌙' : '';
    d.innerHTML = '<div class="message-avatar" style="background:linear-gradient(135deg,' + cat.color + ',' + adjustColor(cat.color, -20) + ');" onmouseenter="showCatTooltip(\'' + cat.id + '\',event)" onmouseleave="hideCatTooltip()">' + cat.emoji + '</div><div class="message-content"><div class="message-sender">' + escapeHtml(cat.name) + nightLabel + '</div><div class="message-bubble" data-real="' + escapeHtml(text) + '">' + displayText + '</div><div class="message-time">' + getTimeStr() + '</div></div>';
    document.getElementById('chatMessages').appendChild(d);
    scrollToBottom();
}
function addThinkingIndicator(cat) {
    var d = document.createElement('div');
    d.className = 'message cat-message';
    d.id = 'thinking-' + cat.id;
    d.innerHTML = '<div class="message-avatar" style="background:linear-gradient(135deg,' + cat.color + ',' + adjustColor(cat.color, -20) + ');" onmouseenter="showCatTooltip(\'' + cat.id + '\',event)" onmouseleave="hideCatTooltip()">' + cat.emoji + '</div><div class="message-content"><div class="message-sender">' + escapeHtml(cat.name) + '</div><div class="message-thinking"><span>' + escapeHtml(cat.name) + ' 正在思考</span><div class="thinking-dots"><span></span><span></span><span></span></div></div></div>';
    document.getElementById('chatMessages').appendChild(d);
    scrollToBottom();
}
function removeThinkingIndicator(catId) {
    var el = document.getElementById('thinking-' + catId);
    if (el) el.remove();
}

// ====================== Send Message ======================
function sendMessage() {
    var input = document.getElementById('messageInput');
    var text = input.value.trim();
    if (!text) return;
    input.value = '';
    autoResize(input);
    addUserMessage(text);
    messages.push({ role:'user', name:'铲屎官', content:text });
    if (cats.length === 0) {
        addSystemMessage('💡 还没有猫猫加入呢～点击左侧「添加一只猫猫」按钮吧！');
        return;
    }
    // Debate mode: sequential speaking
    if (gameMode === 'debate') {
        if (dbState.speaking) {
            showToast('⏳ 猫猫正在发言中，请等待...');
            return;
        }
        if (!dbState.active) {
            // Auto-start debate on first message
            if (cats.length < 2) {
                addSystemMessage('⚠️ 至少需要 2 只猫猫才能辩论！');
                return;
            }
            var maxR = parseInt(document.getElementById('debateRounds').value, 10);
            dbState.maxRounds = maxR;
            dbState.active = true;
            dbState.round = 0;
            dbState.turnIndex = 0;
            dbState.speaking = false;
            if (dbState.order.length === 0) dbState.order = cats.map(function(c) { return c.id; });
            document.getElementById('dbStartBtn').disabled = true;
            document.getElementById('dbNextBtn').disabled = false;
            document.getElementById('dbEndBtn').disabled = false;
            var names = dbState.order.map(function(id) {
                var c = cats.find(function(x) { return x.id === id; });
                return c ? c.emoji + c.name : '';
            }).join(' → ');
            addSystemMessage('🎯 辩论赛自动开始！发言顺序：' + names, 'debate-msg');
        }
        // Start a new round of sequential responses
        dbState.turnIndex = 0;
        dbState.round++;
        addSystemMessage('📢 第 ' + dbState.round + ' 轮辩论开始 — 辩题：' + text, 'debate-turn-msg');
        debateUpdateStatus();
        debateUpdateOrder();
        setTimeout(function() { debateTriggerNextSpeaker(); }, 600);
        return;
    }
    var isNight = (gameMode === 'werewolf' && wfState.active && wfState.phase === 'night');
    // Parse @mentions — if any @catname found, only those cats respond
    var mentionedCats = parseMentions(text);
    var respondingCats = mentionedCats.length > 0 ? mentionedCats : cats;
    respondingCats.forEach(function(cat, idx) {
        if (wfState.active && wfState.eliminated.includes(cat.id)) return;
        setTimeout(function() {
            var payload;
            if (gameMode === 'werewolf' && wfState.active) {
                var role = wfState.roles[cat.id];
                var sys = buildWerewolfSystemPrompt(cat, role);
                payload = { system:sys, messages:[{ role:'user', content:'[法官]: ' + text }] };
            } else {
                payload = buildApiMessages(cat, messages, false);
            }
            triggerCatResponse(cat, payload, isNight);
        }, idx * 800 + Math.random() * 1200);
    });
}

// ====================== Build API Messages ======================
function parseMentions(text) {
    var mentioned = [];
    cats.forEach(function(cat) {
        if (text.indexOf('@' + cat.name) !== -1) {
            mentioned.push(cat);
        }
    });
    return mentioned;
}

function buildApiMessages(cat, msgHistory, isIntro) {
    var systemContent = cat.personality + '\n\n【聊天室规则】\n- 你在一个有多只猫猫和铲屎官的聊天室里。\n- 请用简短自然的口吻回复（30-100字左右）。\n- 可以用"喵"等语气词，但不要每句话都用。\n- 保持自己的性格特点。';
    var history = (isIntro ? msgHistory : msgHistory.slice(-20)).map(function(m) {
        return { role: m.name === cat.name ? 'assistant' : 'user', content: '[' + m.name + ']: ' + m.content };
    });
    return { system: systemContent, messages: history };
}

// ====================== API Call ======================
function triggerCatResponse(cat, chatPayload, isNight) {
    addThinkingIndicator(cat);
    var done = function(reply) {
        removeThinkingIndicator(cat.id);
        if (reply) {
            addCatMessage(cat, reply, isNight || false);
            messages.push({ role:'assistant', name:cat.name, content:reply });
        } else {
            addCatMessage(cat, '喵...（猫猫好像没想好说什么）', isNight || false);
        }
    };
    var fail = function(err) {
        removeThinkingIndicator(cat.id);
        var msg = err.message;
        if (err instanceof TypeError && (msg === 'Failed to fetch' || msg.indexOf('NetworkError') !== -1 || msg.indexOf('fetch') !== -1)) {
            msg = '网络连接失败，可能是浏览器 CORS 跨域限制（该 API 不支持浏览器直接调用）。请检查 API 地址是否正确，或尝试使用支持 CORS 的 API 代理地址。';
        }
        addCatMessage(cat, '😿 喵呜...连接出了问题：' + msg, false);
        console.error('[' + cat.name + '] API Error:', err);
    };
    if (cat.provider === 'claude') {
        callClaudeAPI(cat, chatPayload).then(done).catch(fail);
    } else {
        callOpenAIAPI(cat, chatPayload).then(done).catch(fail);
    }
}

// ---- OpenAI / GLM (both use OpenAI-compatible format) ----
function callOpenAIAPI(cat, payload) {
    var body = {
        model: cat.model,
        messages: [{ role:'system', content:payload.system }].concat(payload.messages),
        max_tokens: 300,
        temperature: 0.85
    };
    return proxyFetch(cat.apiUrl, {
        method: 'POST',
        headers: { 'Content-Type':'application/json', 'Authorization':'Bearer ' + cat.apiKey },
        body: JSON.stringify(body)
    }).then(function(response) {
        if (!response.ok) {
            return response.text().then(function(t) { throw new Error('API (' + response.status + '): ' + t.substring(0, 120)); });
        }
        return response.json();
    }).then(function(data) {
        return (data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content) ? data.choices[0].message.content.trim() : '';
    });
}

// ---- Claude (Anthropic Messages API) ----
function callClaudeAPI(cat, payload) {
    var merged = [];
    payload.messages.forEach(function(m) {
        if (merged.length > 0 && merged[merged.length - 1].role === m.role) {
            merged[merged.length - 1].content += '\n' + m.content;
        } else {
            merged.push({ role:m.role, content:m.content });
        }
    });
    if (merged.length === 0 || merged[0].role !== 'user') {
        merged.unshift({ role:'user', content:'（对话开始）' });
    }
    var final = [];
    var lastRole = null;
    merged.forEach(function(m) {
        if (m.role === lastRole) {
            final.push({ role: lastRole === 'user' ? 'assistant' : 'user', content:'...' });
        }
        final.push(m);
        lastRole = m.role;
    });
    var body = { model:cat.model, max_tokens:300, system:payload.system, messages:final };
    return proxyFetch(cat.apiUrl, {
        method: 'POST',
        headers: {
            'Content-Type':'application/json',
            'x-api-key': cat.apiKey,
            'anthropic-version': cat.claudeVersion || '2023-06-01',
            'anthropic-dangerous-direct-browser-access':'true'
        },
        body: JSON.stringify(body)
    }).then(function(response) {
        if (!response.ok) {
            return response.text().then(function(t) { throw new Error('Claude (' + response.status + '): ' + t.substring(0, 120)); });
        }
        return response.json();
    }).then(function(data) {
        if (data.content && Array.isArray(data.content)) {
            return data.content.filter(function(b) { return b.type === 'text'; }).map(function(b) { return b.text; }).join('\n').trim();
        }
        return '';
    });
}

// ====================== Helpers ======================
var mentionActive = false, mentionIndex = 0, mentionFiltered = [];

function handleInputKeydown(e) {
    if (mentionActive) {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            mentionIndex = (mentionIndex + 1) % mentionFiltered.length;
            renderMentionPopup(mentionFiltered);
            return;
        }
        if (e.key === 'ArrowUp') {
            e.preventDefault();
            mentionIndex = (mentionIndex - 1 + mentionFiltered.length) % mentionFiltered.length;
            renderMentionPopup(mentionFiltered);
            return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
            e.preventDefault();
            if (mentionFiltered.length > 0) {
                selectMention(mentionFiltered[mentionIndex]);
            }
            return;
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            closeMentionPopup();
            return;
        }
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

function handleInputChange(textarea) {
    autoResize(textarea);
    checkMention(textarea);
}

function checkMention(textarea) {
    var val = textarea.value;
    var pos = textarea.selectionStart;
    // Find the @ symbol before cursor
    var before = val.substring(0, pos);
    var atIdx = before.lastIndexOf('@');
    if (atIdx === -1 || (atIdx > 0 && before[atIdx - 1] !== ' ' && before[atIdx - 1] !== '\n')) {
        closeMentionPopup();
        return;
    }
    var query = before.substring(atIdx + 1).toLowerCase();
    // If there's a space after the query started, close
    if (query.indexOf(' ') !== -1 && query.indexOf(' ') < query.length - 1) {
        closeMentionPopup();
        return;
    }
    mentionFiltered = cats.filter(function(c) {
        return c.name.toLowerCase().indexOf(query) !== -1;
    });
    if (mentionFiltered.length === 0) {
        closeMentionPopup();
        return;
    }
    mentionActive = true;
    mentionIndex = 0;
    renderMentionPopup(mentionFiltered);
}

function renderMentionPopup(list) {
    var popup = document.getElementById('mentionPopup');
    popup.innerHTML = list.map(function(cat, idx) {
        return '<div class="mention-item' + (idx === mentionIndex ? ' active' : '') + '" onmousedown="selectMention(cats.find(function(c){return c.id===\'' + cat.id + '\'}))">'
            + '<div class="mention-avatar" style="background:linear-gradient(135deg,' + cat.color + ',' + adjustColor(cat.color, -20) + ');">' + cat.emoji + '</div>'
            + '<span class="mention-name">' + escapeHtml(cat.name) + '</span>'
            + '<span class="mention-model">' + escapeHtml(cat.model) + '</span>'
            + '</div>';
    }).join('');
    popup.classList.add('active');
}

function selectMention(cat) {
    if (!cat) return;
    var textarea = document.getElementById('messageInput');
    var val = textarea.value;
    var pos = textarea.selectionStart;
    var before = val.substring(0, pos);
    var atIdx = before.lastIndexOf('@');
    if (atIdx === -1) return;
    var after = val.substring(pos);
    var newVal = val.substring(0, atIdx) + '@' + cat.name + ' ' + after;
    textarea.value = newVal;
    var newPos = atIdx + 1 + cat.name.length + 1;
    textarea.selectionStart = newPos;
    textarea.selectionEnd = newPos;
    textarea.focus();
    closeMentionPopup();
}

function closeMentionPopup() {
    mentionActive = false;
    mentionIndex = 0;
    mentionFiltered = [];
    var popup = document.getElementById('mentionPopup');
    popup.classList.remove('active');
}
function autoResize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
}
function scrollToBottom() {
    var container = document.getElementById('chatMessages');
    requestAnimationFrame(function() { container.scrollTop = container.scrollHeight; });
}
function hideEmptyState() {
    var el = document.getElementById('emptyState');
    if (el) el.remove();
}
function getTimeStr() {
    return new Date().toLocaleTimeString('zh-CN', { hour:'2-digit', minute:'2-digit' });
}
function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
function adjustColor(hex, amount) {
    var r = parseInt(hex.slice(1, 3), 16);
    var g = parseInt(hex.slice(3, 5), 16);
    var b = parseInt(hex.slice(5, 7), 16);
    r = Math.max(0, Math.min(255, r + amount));
    g = Math.max(0, Math.min(255, g + amount));
    b = Math.max(0, Math.min(255, b + amount));
    return '#' + r.toString(16).padStart(2, '0') + g.toString(16).padStart(2, '0') + b.toString(16).padStart(2, '0');
}
function showToast(message) {
    var container = document.getElementById('toastContainer');
    var toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(function() { toast.remove(); }, 3000);
}
function toggleGlobalSettings() {
    var el = document.getElementById('globalSettings');
    var arrow = document.getElementById('settingsArrow');
    if (el.style.display === 'none') { el.style.display = 'block'; arrow.textContent = '▼'; }
    else { el.style.display = 'none'; arrow.textContent = '▶'; }
}
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}
function toggleCliProxy() {
    var el = document.getElementById('cliProxySettings');
    var arrow = document.getElementById('cliArrow');
    if (el.style.display === 'none') { el.style.display = 'block'; arrow.textContent = '▼'; }
    else { el.style.display = 'none'; arrow.textContent = '▶'; }
}
function onCliProxyToggle() {
    var cb = document.getElementById('cliProxyEnabled');
    var label = document.getElementById('cliProxyLabel');
    cliProxy.enabled = cb.checked;
    if (cb.checked) {
        label.textContent = '已启用';
        label.style.color = '#16a34a';
        cliProxy.url = document.getElementById('cliProxyUrl').value.trim().replace(/\/+$/, '') || 'http://localhost:3456';
        testCliConnection();
    } else {
        label.textContent = '未启用';
        label.style.color = '';
        cliProxy.connected = false;
        updateCliStatusDot('off');
        var st = document.getElementById('cliConnStatus');
        st.style.display = 'none';
    }
}
function testCliConnection() {
    var proxyUrl = (document.getElementById('cliProxyUrl').value.trim().replace(/\/+$/, '') || 'http://localhost:3456');
    cliProxy.url = proxyUrl;
    var st = document.getElementById('cliConnStatus');
    st.style.display = 'block';
    st.className = 'cli-conn-status';
    st.textContent = '🔄 正在连接 ' + proxyUrl + ' ...';
    updateCliStatusDot('off');
    fetch(proxyUrl + '/health', { method: 'GET' })
        .then(function(res) { return res.json(); })
        .then(function(data) {
            if (data.status === 'ok') {
                cliProxy.connected = true;
                st.className = 'cli-conn-status success';
                st.innerHTML = '✅ 连接成功！' + data.name + '<br>已处理 ' + data.requests + ' 个请求 · 运行 ' + Math.floor(data.uptime) + 's';
                updateCliStatusDot('connected');
                showToast('🟢 CLI 代理连接成功！');
            } else {
                throw new Error('意外的响应');
            }
        })
        .catch(function(err) {
            cliProxy.connected = false;
            st.className = 'cli-conn-status error';
            st.innerHTML = '❌ 连接失败！请确认 CLI 已启动<br><code>node cat_chat_cli.js</code>';
            updateCliStatusDot('error');
        });
}
function updateCliStatusDot(state) {
    var dot = document.getElementById('cliStatusDot');
    dot.className = 'cli-status' + (state === 'connected' ? ' connected' : state === 'error' ? ' error' : '');
}
function proxyFetch(url, options) {
    if (!cliProxy.enabled || !cliProxy.connected) {
        return fetch(url, options);
    }
    var proxyBody = {
        targetUrl: url,
        method: options.method || 'POST',
        headers: options.headers || {},
        body: options.body ? JSON.parse(options.body) : undefined
    };
    return fetch(cliProxy.url + '/proxy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(proxyBody)
    });
}

// ====================== Boot ======================
init();

// ====================== Export / Import Cats ======================
function exportCats() {
    if (cats.length === 0) { showToast('⚠️ 没有猫猫可以导出！'); return; }
    var data = {
        version: 1,
        exportTime: new Date().toISOString(),
        cats: cats.map(function(c) {
            return {
                name: c.name,
                emoji: c.emoji,
                color: c.color,
                personality: c.personality,
                provider: c.provider,
                apiUrl: c.apiUrl,
                apiKey: c.apiKey,
                model: c.model,
                claudeVersion: c.claudeVersion,
                badgeClass: c.badgeClass
            };
        })
    };
    var json = JSON.stringify(data, null, 2);
    var blob = new Blob([json], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'catchat_cats_' + new Date().toISOString().slice(0, 10) + '.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast('✅ 已导出 ' + cats.length + ' 只猫猫的配置！');
}

function importCatsClick() {
    document.getElementById('importCatsFile').click();
}

function importCatsFile(event) {
    var file = event.target.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function(e) {
        try {
            var data = JSON.parse(e.target.result);
            if (!data.cats || !Array.isArray(data.cats) || data.cats.length === 0) {
                showToast('❌ 文件格式错误或没有猫猫数据！');
                return;
            }
            var count = 0;
            data.cats.forEach(function(c) {
                if (!c.name || !c.provider) return;
                var cfg = PROVIDERS[c.provider];
                if (!cfg) cfg = PROVIDERS.openai;
                cats.push({
                    id: Date.now().toString() + '_' + count,
                    name: c.name,
                    emoji: c.emoji || '🐱',
                    color: c.color || '#f582ae',
                    personality: c.personality || '',
                    provider: c.provider,
                    apiUrl: c.apiUrl || cfg.defaultUrl,
                    apiKey: c.apiKey || '',
                    model: c.model || cfg.defaultModel,
                    claudeVersion: c.claudeVersion || '2023-06-01',
                    badgeClass: c.badgeClass || cfg.badgeClass
                });
                count++;
            });
            renderMembers();
            updateOnlineCount();
            if (gameMode === 'pipeline') pipelineUpdateRoleAssign();
            if (gameMode === 'debate') debateUpdateOrder();
            addSystemMessage('📥 已导入 ' + count + ' 只猫猫的配置！');
            showToast('✅ 成功导入 ' + count + ' 只猫猫！');
        } catch (err) {
            showToast('❌ 解析文件失败：' + err.message);
        }
    };
    reader.readAsText(file);
    event.target.value = '';
}

// ====================== Cat Tooltip ======================
var catTooltipEl = null;
var catTooltipTimer = null;
function showCatTooltip(catId, event) {
    var cat = cats.find(function(c) { return c.id === catId; });
    if (!cat) return;
    if (catTooltipTimer) { clearTimeout(catTooltipTimer); catTooltipTimer = null; }
    hideCatTooltipNow();
    var tip = document.createElement('div');
    tip.className = 'cat-tooltip';
    tip.setAttribute('data-tooltip-cat', catId);
    tip.onmouseenter = function() { if (catTooltipTimer) { clearTimeout(catTooltipTimer); catTooltipTimer = null; } };
    tip.onmouseleave = function() { scheduleCatTooltipHide(); };
    var cfg = PROVIDERS[cat.provider] || {};
    var rows = '';
    rows += '<div class="tt-row"><span class="tt-label">提供商</span><span class="tt-value">' + (cfg.icon || '') + ' ' + (cfg.name || cat.provider) + '</span></div>';
    rows += '<div class="tt-row"><span class="tt-label">模型</span><span class="tt-value">' + escapeHtml(cat.model) + '</span></div>';
    rows += '<div class="tt-row"><span class="tt-label">API 地址</span><span class="tt-value">' + escapeHtml(cat.apiUrl) + '</span></div>';
    if (cat.provider === 'claude') {
        rows += '<div class="tt-row"><span class="tt-label">API 版本</span><span class="tt-value">' + escapeHtml(cat.claudeVersion || '2023-06-01') + '</span></div>';
    }
    var personalityPreview = cat.personality.length > 120 ? cat.personality.substring(0, 120) + '…' : cat.personality;
    tip.innerHTML = '<div class="tt-header"><div class="tt-avatar" style="background:linear-gradient(135deg,' + cat.color + ',' + adjustColor(cat.color, -20) + ');">' + cat.emoji + '</div><div class="tt-name">' + escapeHtml(cat.name) + '</div><button class="tt-edit-btn" onclick="openEditCatModal(\'' + cat.id + '\')">✏️ 编辑</button></div>' + rows + '<div class="tt-personality">🐾 ' + escapeHtml(personalityPreview) + '</div>';
    document.body.appendChild(tip);
    catTooltipEl = tip;
    // Position tooltip near the avatar
    var rect = event.target.closest('.member-avatar, .message-avatar').getBoundingClientRect();
    var tipW = tip.offsetWidth, tipH = tip.offsetHeight;
    var left = rect.right + 10;
    var top = rect.top;
    if (left + tipW > window.innerWidth - 10) left = rect.left - tipW - 10;
    if (top + tipH > window.innerHeight - 10) top = window.innerHeight - tipH - 10;
    if (top < 10) top = 10;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
}
function scheduleCatTooltipHide() {
    if (catTooltipTimer) clearTimeout(catTooltipTimer);
    catTooltipTimer = setTimeout(function() { hideCatTooltipNow(); catTooltipTimer = null; }, 200);
}
function hideCatTooltip() {
    scheduleCatTooltipHide();
}
function hideCatTooltipNow() {
    if (catTooltipEl) { catTooltipEl.remove(); catTooltipEl = null; }
}

// ====================== Edit Cat Modal ======================
var editCatProvider = 'openai';
function openEditCatModal(catId) {
    hideCatTooltipNow();
    var cat = cats.find(function(c) { return c.id === catId; });
    if (!cat) return;
    document.getElementById('editCatId').value = catId;
    document.getElementById('editCatName').value = cat.name;
    document.getElementById('editCatPersonality').value = cat.personality;
    document.getElementById('editCatApiUrl').value = cat.apiUrl;
    document.getElementById('editCatApiKey').value = cat.apiKey;
    document.getElementById('editCatModel').value = cat.model;
    document.getElementById('editClaudeApiVersion').value = cat.claudeVersion || '2023-06-01';
    editCatProvider = cat.provider;
    // Render provider buttons
    var provHtml = '';
    Object.keys(PROVIDERS).forEach(function(k) {
        var p = PROVIDERS[k];
        provHtml += '<button class="edit-prov-btn ' + (k === cat.provider ? 'selected' : '') + '" data-prov="' + k + '" onclick="editSelectProvider(\'' + k + '\')">' + p.icon + ' ' + p.name + '</button>';
    });
    document.getElementById('editProviderCards').innerHTML = provHtml;
    updateEditProviderUI(cat.provider);
    document.getElementById('editCatModal').classList.add('active');
}
function editSelectProvider(p) {
    editCatProvider = p;
    document.querySelectorAll('#editProviderCards .edit-prov-btn').forEach(function(b) { b.classList.remove('selected'); });
    document.querySelector('#editProviderCards .edit-prov-btn[data-prov="' + p + '"]').classList.add('selected');
    updateEditProviderUI(p);
}
function updateEditProviderUI(p) {
    var cfg = PROVIDERS[p];
    document.getElementById('editCatApiUrl').placeholder = cfg.defaultUrl;
    document.getElementById('editApiUrlHint').textContent = cfg.urlHint;
    document.getElementById('editCatModel').placeholder = cfg.defaultModel;
    var pr = document.getElementById('editModelPresets');
    pr.innerHTML = cfg.models.map(function(m) {
        return '<button class="edit-preset-btn" onclick="document.getElementById(\'editCatModel\').value=\'' + m + '\';">' + m + '</button>';
    }).join('');
    pr.style.display = cfg.models.length ? 'flex' : 'none';
    document.getElementById('editClaudeVersionGroup').style.display = (p === 'claude') ? 'block' : 'none';
}
function closeEditCatModal() {
    document.getElementById('editCatModal').classList.remove('active');
}
function saveEditCat() {
    var catId = document.getElementById('editCatId').value;
    var cat = cats.find(function(c) { return c.id === catId; });
    if (!cat) { showToast('❌ 猫猫不存在'); return; }
    var name = document.getElementById('editCatName').value.trim();
    if (!name) { showToast('⚠️ 名字不能为空！'); return; }
    var provider = editCatProvider;
    var cfg = PROVIDERS[provider];
    var rawUrl = document.getElementById('editCatApiUrl').value.trim();
    var apiUrl = rawUrl ? normalizeApiUrl(rawUrl, provider) : cfg.defaultUrl;
    var apiKey = document.getElementById('editCatApiKey').value.trim();
    if (!apiKey) {
        var gKey = document.getElementById('globalApiKey').value.trim();
        apiKey = gKey || '';
    }
    if (!apiKey) { showToast('⚠️ 请填写 API Key'); return; }
    cat.name = name;
    cat.personality = document.getElementById('editCatPersonality').value.trim() || cat.personality;
    cat.provider = provider;
    cat.apiUrl = apiUrl;
    cat.apiKey = document.getElementById('editCatApiKey').value.trim() || document.getElementById('globalApiKey').value.trim() || '';
    cat.model = document.getElementById('editCatModel').value.trim() || cfg.defaultModel;
    cat.claudeVersion = document.getElementById('editClaudeApiVersion').value.trim() || '2023-06-01';
    cat.badgeClass = cfg.badgeClass;
    renderMembers();
    closeEditCatModal();
    showToast('✅ ' + cat.emoji + ' ' + cat.name + ' 的档案已更新！');
    addSystemMessage('✏️ ' + cat.emoji + ' ' + cat.name + ' 的配置已被修改（' + cfg.icon + ' ' + cfg.name + ' · ' + cat.model + '）');
}