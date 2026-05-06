// 全局鐘舵€?

const APP_STATE = {

    currentView: 'my-scripts',

    currentUser: null,

    currentScript: null,

    currentScriptInfo: null,

    currentEpisode: null,

    currentStep: 0, // 0: 剧本, 1: 主体, 2: 故事鏉?

    library: null,

    cards: [],

    shots: [],

    currentShot: null,

    currentShotVideos: null,

    templates: [],

    soraPromptStyle: '',

    selectedCardForPrompt: null,

    videoPollingInterval: null,  // 瑙嗛鐘舵€佽疆询定时器

    narrationPollingInterval: null,  // 瑙ｈ剧转换状态轮询定时器

    currentNarrationTemplate: null,  // 当前瑙ｈ剧转换模板（临时，刷新丢失）

    openingPollingInterval: null,  // 精彩寮€头生成状态轮询定时器

    currentOpeningTemplate: null,  // 当前精彩寮€头生成模板（临时，刷新丢失）

    imagePollingInterval: null,  // 图片生成鐘舵€佽疆询定时器

    providersStatsPollingInterval: null,  // 服务商统计数鎹疆询定时器

    providersStats: {},  // 服务商统计数鎹?{apimart: {...}, suchuang: {...}, yijia: {...}}

    previousProcessingTasks: new Set(),  // 跟踪之前正在处理的任鍔?(格式: "shot_id:type")

    currentPreviewShotId: null,  // 当前棰勮区域显示的镜头ID

    currentPreviewVideoPath: null,  // 当前棰勮区域显示鐨勮频路寰?

    importBatchesByEpisode: {},

    imageModal: {

        isOpen: false,

        images: [],

        currentIndex: 0,

        cardId: null

    },

    confirmCallback: null,

    subjectScrollTop: 0,

    storyboardScrollTop: 0,

    pageScrollTop: 0,

    selectedReferenceImagesForGeneration: [],  // AI作图选择的参考图IDs

    simpleStoryboardBatchState: null,

    currentStoryboardBoard: 'storyboard1',

    storyboard2MockByEpisode: {},

    storyboard2UiByEpisode: {},

    storyboard2DragSource: null,

    storyboard2AutoScrollActive: false,

    storyboard2DragY: null,

    storyboard2GeneratingBySubShot: {},

    storyboard2VideoGeneratingBySubShot: {},

    storyboard2DeletingByImage: {},

    storyboard2DeletingByVideo: {},

    storyboard2BatchGenerating: false,

    storyboardPromptBatchSubmitting: false,

    storyboardReasoningPromptBatchSubmitting: false,

    simpleStoryboardSubmissionPending: false,

    simpleStoryboardLoadVersion: 0,

    storyboard2GenerationPollingInterval: null,

    storyboard2ShotEditorState: null,

    storyboard2SubShotSubjectEditorState: null,

    storyboard2SavingSceneBySubShot: {},

    detailImagesGenerateModalState: null,

    voiceoverUiByScript: {},

    voiceoverLineSettingsByEpisode: {},

    voiceoverAutosaveTimer: null,

    voiceoverSaveState: 'idle',

    voiceoverLastSavedAt: null,

    voiceoverStatusPollingInterval: null,

    pollingLocks: {},

    videoModelPricing: {},  // Store video model pricing from API

    motiVideoProviderAccounts: { total: 0, records: [] },

    videoGenerationSubmittingByShot: {},

    largeShotTemplates: [],

    largeShotTemplatesLoaded: false,

    largeShotTemplatesLoading: false

};



const APP_STATE_STORAGE_KEY = 'story_creator_state_v1';

const IMPORT_BATCH_STORAGE_KEY = 'story_creator_import_batches_v1';

const VOICEOVER_UI_STORAGE_KEY = 'story_creator_voiceover_ui_v1';

const VOICEOVER_LINE_SETTINGS_STORAGE_KEY = 'story_creator_voiceover_line_settings_v1';

const IMPORT_BATCH_COLORS = ['#2f7edb', '#d28a2f', '#2f9d7e', '#3f8fa6'];

const SIMPLE_STORYBOARD_POLL_INTERVAL_MS = 8000;

const DETAILED_STORYBOARD_POLL_INTERVAL_MS = 10000;

const VIDEO_STATUS_POLL_INTERVAL_MS = 10000;

const IMAGE_STATUS_POLL_INTERVAL_MS = 15000;

const MANAGED_SESSION_POLL_INTERVAL_MS = 10000;

const VOICEOVER_STATUS_POLL_INTERVAL_MS = 6000;

const NARRATION_STATUS_POLL_INTERVAL_MS = 6000;

const OPENING_STATUS_POLL_INTERVAL_MS = 6000;

const STORYBOARD2_GENERATION_POLL_INTERVAL_MS = 6000;

const AI_PROMPT_STATUS_POLL_INTERVAL_MS = 8000;

const CREATE_FROM_STORYBOARD_POLL_INTERVAL_MS = 8000;

let subjectAudioPreviewPlayer = null;

let subjectAudioPreviewButton = null;

let subjectAudioPreviewCardId = null;



async function withPollingGuard(key, callback) {

    if (!APP_STATE.pollingLocks) {

        APP_STATE.pollingLocks = {};

    }

    if (APP_STATE.pollingLocks[key]) {

        return false;

    }

    APP_STATE.pollingLocks[key] = true;

    try {

        await callback();

        return true;

    } finally {

        APP_STATE.pollingLocks[key] = false;

    }

}



function loadImportBatches() {

    const raw = localStorage.getItem(IMPORT_BATCH_STORAGE_KEY);

    if (!raw) return {};

    try {

        const parsed = JSON.parse(raw);

        return parsed && typeof parsed === 'object' ? parsed : {};

    } catch (error) {

        console.error('Failed to parse import batches:', error);

        return {};

    }

}



function saveImportBatches() {

    localStorage.setItem(

        IMPORT_BATCH_STORAGE_KEY,

        JSON.stringify(APP_STATE.importBatchesByEpisode || {})

    );

}



function getImportBatchesForEpisode(episodeId) {

    if (!episodeId) return [];

    const batches = APP_STATE.importBatchesByEpisode?.[episodeId];

    return Array.isArray(batches) ? batches : [];

}



function setImportBatchesForEpisode(episodeId, batches) {

    if (!episodeId) return;

    APP_STATE.importBatchesByEpisode = APP_STATE.importBatchesByEpisode || {};

    APP_STATE.importBatchesByEpisode[episodeId] = Array.isArray(batches) ? batches : [];

    saveImportBatches();

}



function pruneImportBatchesForEpisode(episodeId, shots) {

    if (!episodeId) return;

    const shotIdSet = new Set((shots || []).map(shot => shot.id));

    const batches = getImportBatchesForEpisode(episodeId)

        .map(batch => ({

            ...batch,

            shotIds: (batch.shotIds || []).filter(id => shotIdSet.has(id))

        }))

        .filter(batch => batch.shotIds.length > 0);

    setImportBatchesForEpisode(episodeId, batches);

}



APP_STATE.importBatchesByEpisode = loadImportBatches();



function loadVoiceoverUiByScript() {

    try {

        const raw = localStorage.getItem(VOICEOVER_UI_STORAGE_KEY);

        if (!raw) return {};

        const parsed = JSON.parse(raw);

        return parsed && typeof parsed === 'object' ? parsed : {};

    } catch (error) {

        console.error('Failed to parse voiceover ui storage:', error);

        return {};

    }

}



function saveVoiceoverUiByScript() {

    localStorage.setItem(

        VOICEOVER_UI_STORAGE_KEY,

        JSON.stringify(APP_STATE.voiceoverUiByScript || {})

    );

}



function loadVoiceoverLineSettingsByEpisode() {

    try {

        const raw = localStorage.getItem(VOICEOVER_LINE_SETTINGS_STORAGE_KEY);

        if (!raw) return {};

        const parsed = JSON.parse(raw);

        return parsed && typeof parsed === 'object' ? parsed : {};

    } catch (error) {

        console.error('Failed to parse voiceover line settings storage:', error);

        return {};

    }

}



function saveVoiceoverLineSettingsByEpisode() {

    localStorage.setItem(

        VOICEOVER_LINE_SETTINGS_STORAGE_KEY,

        JSON.stringify(APP_STATE.voiceoverLineSettingsByEpisode || {})

    );

}



APP_STATE.voiceoverUiByScript = loadVoiceoverUiByScript();

APP_STATE.voiceoverLineSettingsByEpisode = loadVoiceoverLineSettingsByEpisode();



// API请求工具

async function apiRequest(url, options = {}) {

    const token = localStorage.getItem('authToken');



    const defaultOptions = {

        headers: {

            'Content-Type': 'application/json',

            ...(token && { 'Authorization': `Bearer ${token}` })

        }

    };



    const mergedOptions = {

        ...defaultOptions,

        ...options,

        headers: {

            ...defaultOptions.headers,

            ...options.headers

        }

    };



    if (options.body instanceof FormData) {

        delete mergedOptions.headers['Content-Type'];

    }



    const response = await fetch(url, mergedOptions);



    if (response.status === 401) {

        localStorage.clear();

        window.location.href = '/';

        return;

    }



    return response;

}



function parseBackendUtcDate(value) {

    const raw = String(value || '').trim();

    if (!raw) return null;



    let normalized = raw.replace(' ', 'T');

    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(normalized)) {

        normalized += 'Z';

    }



    const parsed = new Date(normalized);

    if (Number.isNaN(parsed.getTime())) {

        return null;

    }

    return parsed;

}



function formatBackendUtcToBeijing(value, fallback = '-') {

    const date = parseBackendUtcDate(value);

    if (!date) return fallback;



    return date.toLocaleString('zh-CN', {

        timeZone: 'Asia/Shanghai',

        hour12: false

    });

}



function formatBackendUtcDateToBeijing(value, fallback = '-') {

    const date = parseBackendUtcDate(value);

    if (!date) return fallback;



    return date.toLocaleDateString('zh-CN', {

        timeZone: 'Asia/Shanghai'

    });

}



function setActiveNav(view) {

    const targetView = view === 'public-libraries' ? 'public-libraries' : 'my-scripts';

    document.querySelectorAll('.nav-item, .header-nav-item').forEach(nav => {

        nav.classList.toggle('active', nav.dataset.view === targetView);

    });

}



function setContentTightBottom(enabled) {

    const content = document.getElementById('content');

    if (!content) return;

    content.classList.toggle('tight-bottom', Boolean(enabled));

}



function updateScrollCache() {

    const subjectArea = document.querySelector('.subject-cards-area');

    if (subjectArea) {

        APP_STATE.subjectScrollTop = subjectArea.scrollTop;

    }

    const storyboardArea = document.querySelector('.storyboard-shots-area');

    if (storyboardArea) {

        APP_STATE.storyboardScrollTop = storyboardArea.scrollTop;

    }

    const page = document.scrollingElement || document.documentElement;

    if (page) {

        APP_STATE.pageScrollTop = page.scrollTop;

    }

}



function applySavedScrollPositions(savedState) {

    if (!savedState) return;

    const subjectArea = document.querySelector('.subject-cards-area');

    if (subjectArea && typeof savedState.subjectScrollTop === 'number') {

        subjectArea.scrollTop = savedState.subjectScrollTop;

    }

    const storyboardArea = document.querySelector('.storyboard-shots-area');

    if (storyboardArea && typeof savedState.storyboardScrollTop === 'number') {

        storyboardArea.scrollTop = savedState.storyboardScrollTop;

    }

    const page = document.scrollingElement || document.documentElement;

    if (page && typeof savedState.pageScrollTop === 'number') {

        page.scrollTop = savedState.pageScrollTop;

    }

}



function saveAppState() {

    updateScrollCache();

    const state = {

        currentView: APP_STATE.currentView,

        currentScript: APP_STATE.currentScript,

        currentEpisode: APP_STATE.currentEpisode,

        currentStep: APP_STATE.currentStep,

        currentShotId: APP_STATE.currentShot ? APP_STATE.currentShot.id : null,

        selectedCardForPrompt: APP_STATE.selectedCardForPrompt || null,

        currentStoryboardBoard: APP_STATE.currentStoryboardBoard || 'storyboard1',

        subjectScrollTop: APP_STATE.subjectScrollTop || 0,

        storyboardScrollTop: APP_STATE.storyboardScrollTop || 0,

        pageScrollTop: APP_STATE.pageScrollTop || 0

    };

    localStorage.setItem(APP_STATE_STORAGE_KEY, JSON.stringify(state));

}



function clearSavedAppState() {

    localStorage.removeItem(APP_STATE_STORAGE_KEY);

    localStorage.removeItem(IMPORT_BATCH_STORAGE_KEY);

}



function readSavedAppState() {

    const raw = localStorage.getItem(APP_STATE_STORAGE_KEY);

    if (!raw) return null;

    try {

        return JSON.parse(raw);

    } catch (error) {

        console.error('Failed to parse saved state:', error);

        return null;

    }

}



async function restoreCreationFlow(savedState) {

    APP_STATE.currentScript = savedState.currentScript;

    APP_STATE.currentEpisode = savedState.currentEpisode;

    APP_STATE.currentStep = 0;

    APP_STATE.currentStoryboardBoard = savedState.currentStoryboardBoard || 'storyboard1';



    const response = await apiRequest(`/api/scripts/${savedState.currentScript}/episodes`);

    if (!response || !response.ok) {

        clearSavedAppState();

        await loadView('my-scripts');

        return;

    }

    const episodes = await response.json();

    const episode = episodes.find(ep => ep.id === savedState.currentEpisode);



    if (!episode) {

        await openScript(savedState.currentScript, { silent: true });

        applySavedScrollPositions(savedState);

        return;

    }



    await loadCreationFlow(episode);

    const restoredStep = Math.min(savedState.currentStep, 6);  // support step 6 (voiceover)

    if (restoredStep >= 1 && restoredStep <= 6) {

        await switchStep(restoredStep);

    }



    // 如果鎭㈠鍒版楠?（故事板）并且有保存的镜头ID，恢澶嶉€変腑的镜澶?

    if (restoredStep === 4 && savedState.currentShotId && APP_STATE.shots) {

        const shot = APP_STATE.shots.find(s => s.id === savedState.currentShotId);

        if (shot) {

            APP_STATE.currentShot = shot;

            renderStoryboardShotsGrid();

            renderStoryboardSidebar();

        }

    }



    // 如果鎭㈠鍒版楠?（主体）并且有保存的镜头ID，恢澶嶉€変腑的镜澶?

    if (restoredStep === 3 && savedState.currentShotId && APP_STATE.shots) {

        const shot = APP_STATE.shots.find(s => s.id === savedState.currentShotId);

        if (shot) {

            APP_STATE.currentShot = shot;

            renderStoryboardShotsGrid();

            renderStoryboardSidebar();

        }

    }



    if (savedState.currentStep === 1 && savedState.selectedCardForPrompt) {

        const exists = APP_STATE.cards.some(card => card.id === savedState.selectedCardForPrompt);

        if (exists) {

            await selectCardForPrompt(savedState.selectedCardForPrompt);

        }

    }



    applySavedScrollPositions(savedState);

}



async function restoreAppState() {

    const savedState = readSavedAppState();

    if (!savedState) return false;



    if (savedState.currentView === 'public-libraries') {

        setActiveNav('public-libraries');

        await loadView('public-libraries');

        applySavedScrollPositions(savedState);

        return true;

    }



    if (savedState.currentView === 'script-detail' && savedState.currentScript) {

        setActiveNav('my-scripts');

        await openScript(savedState.currentScript, { silent: true });

        applySavedScrollPositions(savedState);

        return true;

    }



    if (savedState.currentView === 'creation' && savedState.currentScript && savedState.currentEpisode) {

        setActiveNav('my-scripts');

        await restoreCreationFlow(savedState);

        return true;

    }



    if (savedState.currentView === 'my-scripts') {

        setActiveNav('my-scripts');

        await loadView('my-scripts');

        applySavedScrollPositions(savedState);

        return true;

    }



    return false;

}



function clearAnalyzeOverlay() {

    const overlay = document.getElementById('aiAnalyzeLoading');

    if (overlay) {

        overlay.remove();

    }

}



function invalidateAnalyzeState() {

    clearAnalyzeOverlay();

}



function showAnalyzeOverlay() {

    let overlay = document.getElementById('aiAnalyzeLoading');

    if (overlay) {

        return overlay;

    }



    overlay = document.createElement('div');

    overlay.id = 'aiAnalyzeLoading';

    overlay.style.cssText = `

        position: fixed;

        right: 20px;

        top: 80px;

        background: rgba(0, 0, 0, 0.85);

        color: white;

        padding: 12px 14px;

        border-radius: 8px;

        display: flex;

        align-items: center;

        gap: 10px;

        z-index: 9999;

        font-size: 13px;

        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.35);

        pointer-events: none;

    `;

    overlay.innerHTML = `

        <div class="spinner" style="

            border: 3px solid rgba(255, 255, 255, 0.3);

            border-top: 3px solid white;

            border-radius: 50%;

            width: 20px;

            height: 20px;

            animation: spin 1s linear infinite;

        "></div>

        <div>

            <div>正在AI分析剧本...</div>

            <div style="font-size: 11px; margin-top: 2px; opacity: 0.75;">可继续操作</div>

        </div>

    `;

    document.body.appendChild(overlay);



    if (!document.getElementById('spinnerStyle')) {

        const style = document.createElement('style');

        style.id = 'spinnerStyle';

        style.textContent = `

            @keyframes spin {

                0% { transform: rotate(0deg); }

                100% { transform: rotate(360deg); }

            }

        `;

        document.head.appendChild(style);

    }



    setTimeout(() => {

        if (overlay && overlay.parentNode) {

            overlay.remove();

        }

    }, 2000);



    return overlay;

}



// 鍒濆化应鐢?

async function initApp() {

    const token = localStorage.getItem('authToken');

    if (!token) {

        window.location.href = '/';

        return;

    }



    try {

        const response = await apiRequest('/api/auth/verify', { method: 'POST' });

        if (response.ok) {

            APP_STATE.currentUser = await response.json();

            document.getElementById('headerUsername').textContent = APP_STATE.currentUser.username;

            const storedUserId = parseInt(localStorage.getItem('userId') || '0', 10);

            if (storedUserId && storedUserId !== APP_STATE.currentUser.id) {

                clearSavedAppState();

                localStorage.setItem('userId', APP_STATE.currentUser.id);

                localStorage.setItem('username', APP_STATE.currentUser.username);

            }

        } else {

            throw new Error('Token invalid');

        }

    } catch (error) {

        localStorage.clear();

        window.location.href = '/';

        return;

    }



    bindEvents();



    // Load video model pricing from API

    await loadVideoModelPricing();

    await loadMotiVideoProviderAccounts();



    // 鍚姩服务商统计数鎹疆璇?

    startProvidersStatsPolling();



    const restored = await restoreAppState();

    if (!restored) {

        loadView('my-scripts');

    }

}



// 绑定事件

function bindEvents() {

    document.getElementById('logoutButton').addEventListener('click', () => {

        localStorage.clear();

        window.location.href = '/';

    });



    const headerUserTrigger = document.querySelector('.header-right .username');

    if (headerUserTrigger) {

        headerUserTrigger.style.cursor = 'pointer';

        headerUserTrigger.title = '查看额度';

        headerUserTrigger.addEventListener('click', showUserQuotaDialog);

    }



    // 点击鏍囬返回主页

    document.getElementById('headerTitle').addEventListener('click', () => {

        loadView('my-scripts');

    });



    document.querySelectorAll('.nav-item, .header-nav-item').forEach(item => {

        item.addEventListener('click', (e) => {

            e.preventDefault();

            const view = item.dataset.view;



            document.querySelectorAll('.nav-item, .header-nav-item').forEach(nav => nav.classList.remove('active'));

            item.classList.add('active');



            loadView(view);

        });

    });



    bindModalEvents();



    document.getElementById('libraryFormModal').addEventListener('click', (e) => {

        if (e.target.id === 'libraryFormModal') closeLibraryFormModal();

    });



    document.getElementById('confirmModal').addEventListener('click', (e) => {

        if (e.target.id === 'confirmModal') closeConfirmModal();

    });



    window.addEventListener('beforeunload', () => {

        saveAppState();

    });

}



async function showUserQuotaDialog() {

    const username = String(APP_STATE.currentUser?.username || '').trim();

    if (!username) {

        showAlertDialog('无法获取当前用户名');

        return;

    }



    const headers = {

        'Authorization': 'Bearer sk-Zv2THcS1J7KDZkQ-griUI6UlRSNcgQhvTXu70tuvRBw'

    };

    const encodedUsername = encodeURIComponent(username);

    const candidateUrls = [

        `https://ne.mocatter.cn/api_sora/quota/${encodedUsername}`,

        `https://ne.mocatter.cn/quota/${encodedUsername}`

    ];



    let payload = null;

    let lastError = '';



    for (const url of candidateUrls) {

        try {

            const response = await fetch(url, { headers });

            if (!response.ok) {

                lastError = `HTTP ${response.status}`;

                continue;

            }

            payload = await response.json();

            break;

        } catch (error) {

            lastError = error?.message || '请求失败';

        }

    }



    if (!payload) {

        showAlertDialog(`获取额度失败: ${lastError || '未知错误'}`);

        return;

    }



    const dailyQuota = payload.daily_quota ?? '-';

    const usedQuota = payload.used_quota ?? '-';

    const remainingQuota = payload.remaining_quota ?? '-';

    const quotaDate = payload.date || '-';



    showAlertDialog(

        `用户名: ${payload.username || username}\n` +

        `已用额度: ${usedQuota}\n` +

        `当日额度: ${dailyQuota}\n` +

        `剩余额度: ${remainingQuota}\n` +

        `日期: ${quotaDate}`

    );

}



// 绑定图片妯℃€佹事件

function bindModalEvents() {

    const modal = document.getElementById('imageModal');

    const closeBtn = document.getElementById('modalClose');

    const prevBtn = document.getElementById('prevImage');

    const nextBtn = document.getElementById('nextImage');

    const downloadBtn = document.getElementById('downloadImage');

    const deleteBtn = document.getElementById('deleteImage');

    const videoModal = document.getElementById('videoModal');

    const videoCloseBtn = document.getElementById('videoModalClose');



    closeBtn.addEventListener('click', closeImageModal);

    modal.addEventListener('click', (e) => {

        if (e.target === modal) closeImageModal();

    });



    prevBtn.addEventListener('click', () => navigateImage(-1));

    nextBtn.addEventListener('click', () => navigateImage(1));

    downloadBtn.addEventListener('click', downloadCurrentImage);

    deleteBtn.addEventListener('click', deleteCurrentImage);



    if (videoCloseBtn && videoModal) {

        videoCloseBtn.addEventListener('click', closeVideoModal);

        videoModal.addEventListener('click', (e) => {

            if (e.target === videoModal) closeVideoModal();

        });

    }



    document.addEventListener('keydown', (e) => {

        if (!APP_STATE.imageModal.isOpen) return;

        if (e.key === 'ArrowLeft') navigateImage(-1);

        if (e.key === 'ArrowRight') navigateImage(1);

        if (e.key === 'Escape') closeImageModal();

    });



    document.addEventListener('keydown', (e) => {

        if (e.key !== 'Escape') return;

        if (videoModal && videoModal.classList.contains('active')) {

            closeVideoModal();

        }

    });

}



// 加载视图

async function loadView(view) {

    invalidateAnalyzeState();

    APP_STATE.currentView = view;

    APP_STATE.currentScript = null;

    APP_STATE.currentScriptInfo = null;

    APP_STATE.currentEpisode = null;

    APP_STATE.soraPromptStyle = '';

    APP_STATE.currentShot = null;

    APP_STATE.selectedCardForPrompt = null;

    setActiveNav(view);

    const content = document.getElementById('content');

    content.classList.remove('tight-top');
    content.classList.remove('no-scroll');

    setContentTightBottom(false);

    saveAppState();



    // 清空header鍔ㄦ€佸唴瀹?

    document.getElementById('headerSubtitle').innerHTML = '';

    document.getElementById('headerActions').innerHTML = '';



    if (view === 'my-scripts') {

        await loadMyScripts();

    } else if (view === 'public-libraries') {

        await loadPublicLibraries();

    } else if (view === 'hit-dramas') {

        await loadHitDramas();

    }

}



// 加载我的剧本列表

async function loadMyScripts() {

    const content = document.getElementById('content');



    content.innerHTML = `

        <div class="page-header">

            <h2 class="page-title">我的剧本</h2>

            <p class="page-subtitle">创建和管理您的剧本</p>

        </div>

        <div class="page-action-row">
            <button class="primary-button" onclick="createNewScript()">新建剧本</button>
            <button class="secondary-button" onclick="loadView('hit-dramas')">爆款库</button>
        </div>

        <div class="libraries-grid" id="scriptsGrid">

            <div class="loading">加载中...</div>

        </div>

    `;



    try {

        const response = await apiRequest('/api/scripts/my');

        const scripts = await response.json();



        const grid = document.getElementById('scriptsGrid');



        if (scripts.length === 0) {

            grid.innerHTML = `

                <div class="empty-state">

                    <div class="empty-state-text">还没有剧本，点击上方按钮创建一个吧！</div>

                </div>

            `;

        } else {

            grid.innerHTML = scripts.map(script => `

                <div class="library-card" onclick="openScript(${script.id})">

                    <div class="library-card-header">

                        <div class="library-name">${escapeHtml(script.name)}</div>

                        <button class="card-delete" onclick="event.stopPropagation(); deleteScript(${script.id})" title="删除剧本">×</button>

                    </div>

                    <div class="library-meta">

                        <span>${formatBackendUtcDateToBeijing(script.created_at, '')}</span>

                    </div>

                </div>

            `).join('');

        }

    } catch (error) {

        console.error('Failed to load scripts:', error);

        document.getElementById('scriptsGrid').innerHTML = '<div class="empty-state">加载失败</div>';

    }

}



// 创建新剧鏈?- 直接进入创作界面

async function createNewScript() {

    // 直接进入创作流程

    APP_STATE.currentScript = null;

    APP_STATE.currentEpisode = null;

    APP_STATE.currentStep = 0;



    await loadCreationFlow(null);

}



// 删除剧本

async function deleteScript(scriptId) {

    const confirmed = await showConfirmModal('确定要删除这个剧本吗？删除后将无法恢复，包括所有片段、镜头和素材。', '删除剧本');

    if (!confirmed) return;



    try {

        const response = await apiRequest(`/api/scripts/${scriptId}`, {

            method: 'DELETE'

        });



        if (response.ok) {

            showToast('剧本已删除', 'success');

            await loadMyScripts();

        } else {

            const error = await response.json();

            if (shouldShowStoryboardVideoWaitDialog(error.detail)) {
                showAlertDialog(error.detail);
            } else {
                alert(`删除失败: ${error.detail || '未知错误'}`);
            }

        }

    } catch (error) {

        console.error('Failed to delete script:', error);

        alert('删除失败');

    }

}



// 删除鐗囨

async function deleteEpisode(episodeId) {

    const confirmed = await showConfirmModal('确定要删除这个片段吗？删除后将无法恢复，包括所有镜头和素材。', '删除片段');

    if (!confirmed) return;



    try {

        const response = await apiRequest(`/api/episodes/${episodeId}`, {

            method: 'DELETE'

        });



        if (response.ok) {

            showToast('片段已删除', 'success');



            // 如果删除的是当前鐗囨，返回剧鏈垪琛?

            if (APP_STATE.currentEpisode === episodeId) {

                APP_STATE.currentEpisode = null;

                await openScript(APP_STATE.currentScript);

            } else {

                // 否则鍙埛新片段列琛?

                await openScript(APP_STATE.currentScript);

            }

        } else {

            const error = await response.json();

            alert(`删除失败: ${error.detail || '未知错误'}`);

        }

    } catch (error) {

        console.error('Failed to delete episode:', error);

        alert('删除失败');

    }

}



// 打开剧本（显示片段列琛級

async function openScript(scriptId, options = {}) {

    APP_STATE.currentScript = scriptId;

    APP_STATE.currentView = 'script-detail';

    saveAppState();

    const content = document.getElementById('content');

    content.classList.remove('tight-top');

    setContentTightBottom(false);

    const silent = Boolean(options && options.silent);



    // 清空header鍔ㄦ€佸唴瀹?

    document.getElementById('headerSubtitle').innerHTML = '';

    document.getElementById('headerActions').innerHTML = '';



    content.innerHTML = '<div class="loading">加载中...</div>';



    try {

        const [scriptResponse, episodesResponse] = await Promise.all([

            apiRequest(`/api/scripts/${scriptId}`),

            apiRequest(`/api/scripts/${scriptId}/episodes`)

        ]);



        if (!scriptResponse || !episodesResponse || !scriptResponse.ok || !episodesResponse.ok) {

            clearSavedAppState();

            if (!silent) {

                showToast('无权限或剧本不存在', 'error');

            }

            await loadView('my-scripts');

            return;

        }



        const script = await scriptResponse.json();

        const episodes = await episodesResponse.json();



        content.innerHTML = `

            <div class="page-header">

                <h2 class="page-title">${escapeHtml(script.name)}</h2>

                <p class="page-subtitle">选择片段或创建新片段</p>

            </div>

            <div class="library-actions">

                <button class="secondary-button" onclick="loadView('my-scripts')">返回</button>

                <button class="secondary-button" onclick="showCopyScriptModal(${scriptId})">复制剧本</button>

                <button class="primary-button" onclick="createNewEpisode()">新建片段</button>

            </div>

            <div class="episodes-list" id="episodesList"></div>

        `;



        const list = document.getElementById('episodesList');

        if (episodes.length === 0) {

            list.innerHTML = '<div class="empty-state"><div class="empty-state-text">暂无片段</div></div>';

        } else {

            list.innerHTML = episodes.map(ep => `

                <div class="episode-item" onclick="openEpisode(${ep.id})">

                    <div>

                        <div class="episode-name">${escapeHtml(ep.name)}</div>

                        <div class="episode-info">${formatBackendUtcToBeijing(ep.created_at, '')}</div>

                    </div>

                    <button class="card-delete" onclick="event.stopPropagation(); deleteEpisode(${ep.id})" title="删除片段">×</button>

                </div>

            `).join('');

        }

    } catch (error) {

        console.error('Failed to load script:', error);

        content.innerHTML = '<div class="empty-state">加载失败</div>';

    }

}



// 创建新片段

async function createNewEpisode() {

    const name = await showInputModal('新建片段', '请输入片段名称', '', '例如：S01');

    if (!name) return;



    try {

        const response = await apiRequest(`/api/scripts/${APP_STATE.currentScript}/episodes`, {

            method: 'POST',

            body: JSON.stringify({ name: name.trim(), content: '' })

        });



        if (response.ok) {

            const episode = await response.json();

            openEpisode(episode.id);

        } else {

            alert('创建失败');

        }

    } catch (error) {

        console.error('Failed to create episode:', error);

        alert('创建失败');

    }

}



// 打开鐗囨（进入三界面流程锛?

async function openEpisode(episodeId) {

    APP_STATE.currentEpisode = episodeId;

    APP_STATE.currentStep = 0;

    APP_STATE.currentStoryboardBoard = 'storyboard1';

    APP_STATE.currentView = 'creation';

    saveAppState();



    const content = document.getElementById('content');

    content.innerHTML = '<div class="loading">加载中...</div>';



    try {

        const response = await apiRequest(`/api/scripts/${APP_STATE.currentScript}/episodes`);

        if (!response || !response.ok) {

            clearSavedAppState();

            showToast('无权限或片段不存在', 'error');

            await loadView('my-scripts');

            return;

        }

        const episodes = await response.json();

        const episode = episodes.find(ep => ep.id === episodeId);



        if (!episode) throw new Error('片段不存在');



        await loadCreationFlow(episode);

    } catch (error) {

        console.error('Failed to open episode:', error);

        content.innerHTML = '<div class="empty-state">加载失败</div>';

    }

}



// 加载创作流程界面

async function loadCreationFlow(episode) {

    const content = document.getElementById('content');

    content.classList.add('tight-top');

    setContentTightBottom(false);

    APP_STATE.currentView = 'creation';

    saveAppState();



    const title = episode ? escapeHtml(episode.name) : '新建剧本';



    // 更新header鏍囬

    document.getElementById('headerSubtitle').innerHTML = `<span> / ${title}</span>`;



    // 更新header操作按钮

    document.getElementById('headerActions').innerHTML = `

        <button class="secondary-button" onclick="backToScriptList()">返回</button>

        <button class="secondary-button" id="prevStepBtn" onclick="prevStep()" style="display:none;">上一步</button>

        <button class="secondary-button" id="nextStepBtn" onclick="nextStep()">下一步</button>

    `;



    content.innerHTML = `

        <div class="creation-tabs" style="position: sticky; top: 0; z-index: 1000; background: #1a1a1a; padding: 8px 0;">

            <button class="creation-tab active" data-step="0">剧本</button>

            <button class="creation-tab" data-step="1">简单分镜</button>

            <button class="creation-tab" data-step="2">详细分镜</button>

            <button class="creation-tab" data-step="3">主体</button>

            <button class="creation-tab" data-step="4">\u6545\u4E8B\u677F\uFF08sora\uFF09</button>

            <button class="creation-tab" data-step="5">爆款库</button>

            <button class="creation-tab" data-step="6">\u6545\u4E8B\u677F2</button>

            <button class="creation-tab" data-step="7">\u914D\u97F3\u8868</button>

        </div>



        <div id="creationContainer"></div>

    `;



    // 绑定tab切换

    document.querySelectorAll('.creation-tab').forEach(tab => {

        tab.addEventListener('click', () => {

            const step = parseInt(tab.dataset.step);

            switchStep(step);

        });

    });



    // 加载绗竴姝?

    await switchStep(0);

}



// 切换姝ラ

async function switchStep(step) {

    invalidateAnalyzeState();



    // 如果离开主体界面锛堟楠?锛夛紝鍋滄图片杞

    if (APP_STATE.currentStep === 2 && step !== 2) {

        stopImageStatusPolling();

    }

    if (APP_STATE.currentStep === 6 && step !== 6) {

        stopStoryboard2GenerationPolling();

    }

    if (APP_STATE.currentStep === 7 && step !== 7 && APP_STATE.voiceoverStatusPollingInterval) {

        clearInterval(APP_STATE.voiceoverStatusPollingInterval);

        APP_STATE.voiceoverStatusPollingInterval = null;

    }



    APP_STATE.currentStep = step;

    saveAppState();



    // 更新tab鐘舵€?

    document.querySelectorAll('.creation-tab').forEach((tab, index) => {

        tab.classList.toggle('active', index === step);

    });



    // 根据姝ラ控制页面滚动

    const content = document.getElementById('content');

    if (step === 1 || step === 2 || step === 4 || step === 6 || step === 7) {

        content.classList.add('no-scroll');

    } else {

        content.classList.remove('no-scroll');

    }



    // 更新按钮

    const prevBtn = document.getElementById('prevStepBtn');

    const nextBtn = document.getElementById('nextStepBtn');



    prevBtn.style.display = step > 0 ? 'inline-block' : 'none';

    nextBtn.textContent = step === 7 ? '\u5B8C\u6210' : '\u4E0B\u4E00\u6B65';



    // 加载对应姝ラ鍐呭

    const container = document.getElementById('creationContainer');



    if (step === 0) {

        await loadScriptStep();

    } else if (step === 1) {

        await loadSimpleStoryboardStep();  // 鏂板：简单分镜界闈?

    } else if (step === 2) {

        await loadStoryboardTableStep();  // 详细分镜界面（原分镜琛級

    } else if (step === 3) {

        await loadSubjectStep();

    } else if (step === 4) {

        await loadStoryboardStep();

    } else if (step === 5) {
        await loadHitDramasStep();
    } else if (step === 6) {
        await loadStoryboard2Step();
    } else if (step === 7) {
        await loadVoiceoverTableStep();
    }
}



// 绗?步：剧本界面

async function loadScriptStep() {

    const container = document.getElementById('creationContainer');

    setContentTightBottom(false);



    // 妫€查是新建还是编辑

    const isNewScript = !APP_STATE.currentScript;



    if (isNewScript) {

        // 新建模式锛氶€夋嫨剧本 + 鐗囨鍚?+ 鏂囨

        // 获取用户的所有剧鏈?

        let scripts = [];

        try {

            const response = await apiRequest('/api/scripts/my');

            scripts = await response.json();

        } catch (error) {

            console.error('Failed to load scripts:', error);

        }



        container.innerHTML = `

            <div class="script-form">

                <div class="form-group">

                    <label class="form-label">选择或新建剧本</label>

                    <select class="form-input" id="scriptSelector">

                        <option value="">-- 新建剧本 --</option>

                        ${scripts.map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('')}

                    </select>

                </div>

                <div class="form-group" id="newScriptNameGroup">

                    <label class="form-label">新剧本名称</label>

                    <input type="text" class="form-input" id="newScriptName" placeholder="输入剧本名称">

                </div>

                <div class="form-group">

                    <label class="form-label">片段名</label>

                    <input type="text" class="form-input" id="episodeName" placeholder="例如：S01">

                </div>

                <div class="form-group">

                    <label class="form-label">时长规格</label>

                    <select class="form-input" id="storyboard2Duration">

                        <option value="15">4~5短句</option>

                        <option value="25">对话30字</option>

                        <option value="35" selected>规则分段</option>

                    </select>

                </div>

                <div class="form-group">

                    <label class="form-label">文案</label>

                    <textarea class="form-textarea" id="episodeContent" rows="15" placeholder="输入文案内容"></textarea>

                </div>

            </div>

        `;



        // 绑定事件锛氶€夋嫨器变化时显示/隐藏新剧鏈悕称输鍏ユ

        const selector = document.getElementById('scriptSelector');

        const newNameGroup = document.getElementById('newScriptNameGroup');



        selector.addEventListener('change', () => {

            if (selector.value === '') {

                newNameGroup.style.display = 'block';

            } else {

                newNameGroup.style.display = 'none';

            }

        });



    } else {

        // 编辑模式：显示现有片段数鎹?

        const response = await apiRequest(`/api/scripts/${APP_STATE.currentScript}/episodes`);

        const episodes = await response.json();

        const episode = episodes.find(ep => ep.id === APP_STATE.currentEpisode);



        // 杞崲鐘舵€佹彁绀?

        let statusHtml = '';

        let buttonText = '转换为解说剧';

        let buttonDisabled = '';



        if (episode.narration_converting) {

            // 正在杞崲涓?

            statusHtml = '<div style="margin-top: 8px; padding: 8px 12px; background: #2a5a2a; border-radius: 4px; font-size: 13px; color: #4ade80;">正在转换为解说剧，请稍后...</div>';

            buttonText = '转换中...';

            buttonDisabled = 'disabled';

            // 鍚姩杞锛堝果还没启鍔級

            if (!APP_STATE.narrationPollingInterval) {

                startNarrationPolling();

            }

        } else if (episode.narration_error) {

            // 杞崲失败

            statusHtml = `<div style="margin-top: 8px; padding: 8px 12px; background: #5a2a2a; border-radius: 4px; font-size: 13px; color: #f87171;">转换失败: ${escapeHtml(episode.narration_error)}</div>`;

            buttonText = '转换为解说剧';

            // 杞崲失败时停止轮璇?

            stopNarrationPolling();

        } else {

            // 没有杞崲或转换完鎴?

            buttonText = '转换为解说剧';

            // 鍋滄杞

            stopNarrationPolling();

        }



        // 精彩寮€头状态提绀?

        let openingStatusHtml = '';

        let openingButtonText = '生成精彩开头';

        let openingButtonDisabled = '';



        if (episode.opening_generating) {

            // 正在生成涓?

            openingStatusHtml = '<div style="margin-top: 8px; padding: 8px 12px; background: #2a5a2a; border-radius: 4px; font-size: 13px; color: #4ade80;">正在生成精彩开头，请稍后...</div>';

            openingButtonText = '生成中...';

            openingButtonDisabled = 'disabled';

            // 鍚姩杞锛堝果还没启鍔級

            if (!APP_STATE.openingPollingInterval) {

                startOpeningPolling();

            }

        } else if (episode.opening_error) {

            // 生成失败

            openingStatusHtml = `<div style="margin-top: 8px; padding: 8px 12px; background: #5a2a2a; border-radius: 4px; font-size: 13px; color: #f87171;">生成失败: ${escapeHtml(episode.opening_error)}</div>`;

            openingButtonText = '生成精彩开头';

            stopOpeningPolling();

        } else if (episode.opening_content) {

            // 已生鎴?

            openingStatusHtml = `<div style="margin-top: 8px; padding: 8px 12px; background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 4px; font-size: 13px; color: #e0e0e0; white-space: pre-wrap;">${escapeHtml(episode.opening_content)}</div>`;

            openingButtonText = '重新生成开头';

            stopOpeningPolling();

        } else {

            // 鏈敓鎴?

            openingButtonText = '生成精彩开头';

            stopOpeningPolling();

        }



        container.innerHTML = `

            <div class="script-form">

                <div class="form-group">

                    <label class="form-label">片段名</label>

                    <input type="text" class="form-input" id="episodeName" value="${escapeHtml(episode.name)}">

                </div>

                <div class="form-group">

                    <label class="form-label">时长规格</label>

                    <select class="form-input" id="storyboard2Duration">

                        <option value="15" ${episode.storyboard2_duration === 15 ? 'selected' : ''}>4~5短句</option>

                        <option value="25" ${episode.storyboard2_duration === 25 ? 'selected' : ''}>对话30字</option>

                        <option value="35" ${(episode.storyboard2_duration === 35 || !episode.storyboard2_duration) ? 'selected' : ''}>规则分段</option>

                    </select>

                </div>

                <div class="form-group">

                    <label class="form-label">文案</label>

                    <textarea class="form-textarea" id="episodeContent" rows="15" placeholder="输入文案内容">${escapeHtml(episode.content)}</textarea>

                    <div style="display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-top: 8px;">

                        <button onclick="editNarrationTemplate()" style="background: #2a2a2a; color: #fff; border: 1px solid #444; padding: 8px 16px; border-radius: 4px; font-size: 13px; cursor: pointer;">设置提示词</button>

                        <button onclick="convertToNarration()" style="background: white; color: #000; border: 1px solid #444; padding: 8px 16px; border-radius: 4px; font-size: 13px; cursor: pointer;" ${buttonDisabled}>${buttonText}</button>

                    </div>

                    ${statusHtml}

                    <div style="display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-top: 16px;">

                        <button onclick="editOpeningTemplate()" style="background: #2a2a2a; color: #fff; border: 1px solid #444; padding: 8px 16px; border-radius: 4px; font-size: 13px; cursor: pointer;">精彩开头提示词设置</button>

                        <button onclick="generateOpening()" style="background: white; color: #000; border: 1px solid #444; padding: 8px 16px; border-radius: 4px; font-size: 13px; cursor: pointer;" ${openingButtonDisabled}>${openingButtonText}</button>

                    </div>

                    ${openingStatusHtml}

                </div>

            </div>

        `;

    }

}



// 杞崲文本为解说剧

async function convertToNarration() {

    if (!APP_STATE.currentScript || !APP_STATE.currentEpisode) {

        alert('无法获取当前剧本或片段信息');

        return;

    }



    // 读取当前文本框内瀹?

    const textarea = document.getElementById('episodeContent');

    if (!textarea) {

        alert('无法获取文本框');

        return;

    }



    const content = textarea.value.trim();

    if (!content) {

        alert('请先输入文案内容');

        return;

    }



    const button = event.target;



    try {

        // 鍑嗗请求数据

        const requestData = { content };



        // 如果有临时模板，鍙戦€佺粰鍚庣

        if (APP_STATE.currentNarrationTemplate !== null) {

            requestData.template = APP_STATE.currentNarrationTemplate;

        }



        const response = await apiRequest(

            `/api/scripts/${APP_STATE.currentScript}/episodes/${APP_STATE.currentEpisode}/convert-to-narration`,

            {

                method: 'POST',

                headers: {

                    'Content-Type': 'application/json'

                },

                body: JSON.stringify(requestData)

            }

        );



        if (!response.ok) {

            const error = await response.json();

            throw new Error(error.detail || '鍚姩杞崲失败');

        }



        const result = await response.json();

        if (result.success) {

            showToast('文本转解说剧任务已启动', 'success');

            // 鍚姩杞

            startNarrationPolling();

            // 刷新页面显示鐘舵€?

            await loadScriptStep();

        } else {

            throw new Error('鍚姩杞崲失败');

        }

    } catch (error) {

        console.error('Convert to narration failed:', error);

        showToast(error.message || '启动转换失败', 'error');

    }

}



// 鍚姩瑙ｈ剧转换状态轮璇?

function startNarrationPolling() {

    console.log('[瑙ｈ剧轮璇 鍚姩杞');



    // 如果已有杞，先鍋滄

    if (APP_STATE.narrationPollingInterval) {

        clearInterval(APP_STATE.narrationPollingInterval);

    }



    // 姣?绉掓查一娆?

    APP_STATE.narrationPollingInterval = setInterval(async () => {

        await checkNarrationConversionStatus();

    }, NARRATION_STATUS_POLL_INTERVAL_MS);



    // 立即鎵ц涓€娆?

    checkNarrationConversionStatus();

}



// 鍋滄瑙ｈ剧转换状态轮璇?

function stopNarrationPolling() {

    if (APP_STATE.narrationPollingInterval) {

        clearInterval(APP_STATE.narrationPollingInterval);

        APP_STATE.narrationPollingInterval = null;

        console.log('[瑙ｈ剧轮璇 鍋滄杞');

    }

}



async function fetchEpisodePollStatus(episodeId) {

    const response = await apiRequest(`/api/episodes/${episodeId}/poll-status`);

    if (!response.ok) {

        throw new Error(`poll status request failed: ${response.status}`);

    }

    return response.json();

}



// 妫€查文鏈浆瑙ｈ剧状鎬?

async function checkNarrationConversionStatus() {

    if (!APP_STATE.currentEpisode) {

        return;

    }



    await withPollingGuard('narrationStatus', async () => {

        try {

            const episode = await fetchEpisodePollStatus(APP_STATE.currentEpisode);



            if (episode.narration_converting) {

                // 还在转换中，继续轮询

            } else {

                // 转换完成，停止轮询

                stopNarrationPolling();



                if (episode.narration_error) {

                    showToast(`转换失败: ${episode.narration_error}`, 'error');

                } else {

                    showToast('文本转解说剧完成', 'success');

                }



                // 重新渲染编辑鍣紙更新按钮鐘舵€佸拰文本鍐呭锛?

                await loadScriptStep();

            }

        } catch (error) {

            console.error('Check narration status failed:', error);

        }

    });

}



// 编辑瑙ｈ剧转换提示词模板（前绔复时保存）

async function editNarrationTemplate() {

    if (!APP_STATE.currentScript) return;



    try {

        // 读取全局榛樿模板

        const response = await apiRequest('/api/global-settings/narration_conversion_template');

        const setting = await response.json();

        const globalTemplate = setting.value || '';



        // 对话框显示：鍓嶇临时鐘舵€?> 全局榛樿

        const currentTemplate = APP_STATE.currentNarrationTemplate !== null

            ? APP_STATE.currentNarrationTemplate

            : globalTemplate;



        const newTemplate = await showTextareaModal(

            '文本转解说剧提示词设置（临时）',

            '设置转换提示词（仅本次会话有效，刷新后恢复默认）',

            currentTemplate,

            '默认显示全局模板，可自行修改'

        );



        if (newTemplate === null) {

            return;

        }



        // 保存到前绔复时状鎬?

        APP_STATE.currentNarrationTemplate = newTemplate;

        showToast('解说剧转换提示词已更新（临时）', 'success');

    } catch (error) {

        console.error('Failed to load global narration template:', error);

        showToast('加载全局模板失败', 'error');

    }

}



// 编辑精彩寮€头提示词模板（前绔复时保存）

async function editOpeningTemplate() {

    if (!APP_STATE.currentScript) return;



    // 榛樿模板

    const defaultTemplate = '我想把这个片段做成一个短视频，需要一个精彩吸引人的开头，请你帮我写一个开头。';

    let globalTemplate = defaultTemplate;



    try {

        // 读取全局榛樿模板

        const response = await apiRequest('/api/global-settings/opening_generation_template');

        const setting = await response.json();

        globalTemplate = setting.value || defaultTemplate;

    } catch (error) {

        console.error('Failed to load global opening template:', error);

        // 如果加载失败，使用默认模板，不阻姝㈠璇濇显示

        globalTemplate = defaultTemplate;

    }



    try {

        // 对话框显示：鍓嶇临时鐘舵€?> 全局榛樿

        const currentTemplate = APP_STATE.currentOpeningTemplate !== null

            ? APP_STATE.currentOpeningTemplate

            : globalTemplate;



        const newTemplate = await showTextareaModal(

            '精彩开头生成提示词设置（临时）',

            '设置生成提示词（仅本次会话有效，刷新后恢复默认）',

            currentTemplate,

            '默认显示全局模板，可自行修改'

        );



        if (newTemplate === null) {

            return;

        }



        // 保存到前绔复时状鎬?

        APP_STATE.currentOpeningTemplate = newTemplate;

        showToast('精彩开头生成提示词已更新（临时）', 'success');

    } catch (error) {

        console.error('Failed to show modal:', error);

        showToast('打开设置对话框失败', 'error');

    }

}



// 生成精彩寮€澶?

async function generateOpening() {

    if (!APP_STATE.currentScript || !APP_STATE.currentEpisode) {

        alert('无法获取当前剧本或片段信息');

        return;

    }



    // 读取当前文本框内瀹?

    const textarea = document.getElementById('episodeContent');

    if (!textarea) {

        alert('无法获取文本框');

        return;

    }



    const content = textarea.value.trim();

    if (!content) {

        alert('请先输入文案内容');

        return;

    }



    try {

        // 鍑嗗请求数据

        const requestData = { content };



        // 如果有临时模板，鍙戦€佺粰鍚庣

        if (APP_STATE.currentOpeningTemplate !== null) {

            requestData.template = APP_STATE.currentOpeningTemplate;

        }



        const response = await apiRequest(

            `/api/scripts/${APP_STATE.currentScript}/episodes/${APP_STATE.currentEpisode}/generate-opening`,

            {

                method: 'POST',

                headers: {

                    'Content-Type': 'application/json'

                },

                body: JSON.stringify(requestData)

            }

        );



        if (!response.ok) {

            const error = await response.json();

            throw new Error(error.detail || '鍚姩生成失败');

        }



        showToast('精彩开头生成任务已启动，请稍后...', 'success');

        // 鍚姩杞

        startOpeningPolling();

        // 刷新页面显示鐘舵€?

        await loadScriptStep();

    } catch (error) {

        console.error('Generate opening failed:', error);

        showToast(error.message || '启动生成失败', 'error');

    }

}



// 鍚姩精彩寮€头生成状态轮璇?

function startOpeningPolling() {

    console.log('[精彩寮€头轮璇 鍚姩杞');



    // 如果已有杞，先鍋滄

    if (APP_STATE.openingPollingInterval) {

        clearInterval(APP_STATE.openingPollingInterval);

    }



    // 姣?绉掓查一娆?

    APP_STATE.openingPollingInterval = setInterval(async () => {

        await checkOpeningGenerationStatus();

    }, OPENING_STATUS_POLL_INTERVAL_MS);



    // 立即鎵ц涓€娆?

    checkOpeningGenerationStatus();

}



// 鍋滄精彩寮€头生成状态轮璇?

function stopOpeningPolling() {

    if (APP_STATE.openingPollingInterval) {

        clearInterval(APP_STATE.openingPollingInterval);

        APP_STATE.openingPollingInterval = null;

        console.log('[精彩寮€头轮璇 鍋滄杞');

    }

}



// 妫€查精彩开头生成状鎬?

async function checkOpeningGenerationStatus() {

    if (!APP_STATE.currentEpisode) {

        return;

    }



    await withPollingGuard('openingStatus', async () => {

        try {

            const episode = await fetchEpisodePollStatus(APP_STATE.currentEpisode);



            if (episode.opening_generating) {

                // 仍在生成中，继续轮询

            } else {

                // 生成完成或失败，停止轮询

                stopOpeningPolling();



                if (episode.opening_error) {

                    showToast(`生成失败: ${episode.opening_error}`, 'error');

                } else if (episode.opening_content) {

                    showToast('精彩开头生成完成', 'success');

                }



                // 重新渲染编辑鍣紙更新按钮鐘舵€佸拰显示生成的内容）

                await loadScriptStep();

            }

        } catch (error) {

            console.error('Check opening status failed:', error);

        }

    });

}





// 更新风格模板鍐呭（当选择模板时）

function updateStyleTemplateContent() {

    const selector = document.getElementById('styleTemplateSelector');

    const textarea = document.getElementById('styleTemplateContent');

    if (!selector || !textarea) return;



    const selectedOption = selector.options[selector.selectedIndex];

    if (selectedOption && selectedOption.value) {

        const content = selectedOption.getAttribute('data-content');

        textarea.value = content || '';

    }

}



// 保存当前风格为模鏉?

async function saveCurrentStyleAsTemplate() {

    const textarea = document.getElementById('styleTemplateContent');

    if (!textarea) return;



    const content = textarea.value.trim();

    if (!content) {

        showToast('请先输入风格描述', 'warning');

        return;

    }



    const name = await showInputModal('保存为模板', '请输入模板名称', '', '例如：日漫风格');

    if (!name) return;



    try {

        const response = await apiRequest('/api/style-templates', {

            method: 'POST',

            body: JSON.stringify({ name: name.trim(), content: content })

        });



        if (response.ok) {

            showToast('模板已保存', 'success');

            // 重新加载剧本界面以更新模板列琛?

            await loadScriptStep();

        } else {

            showToast('保存失败', 'error');

        }

    } catch (error) {

        console.error('Failed to save style template:', error);

        showToast('保存失败', 'error');

    }

}



// 绠€单分镜列配置（只有镜号和原文锛?

const SIMPLE_STORYBOARD_COLUMNS = {

    shot_number: { label: '镜号', visible: true, required: true, width: 80 },

    original_text: { label: '原剧本段落', visible: true, width: 600 }

};



// 鏂版楠?：简单分镜界闈?

function getSimpleStoryboardBatchState(data = {}) {

    const state = {

        generating: Boolean(data.generating),

        error: String(data.error || ''),

        shotsCount: Number(data.shots_count || (Array.isArray(data.shots) ? data.shots.length : 0)),

        totalBatches: Number(data.total_batches || 0),

        completedBatches: Number(data.completed_batches || 0),

        failedBatches: Number(data.failed_batches || 0),

        submittingBatches: Number(data.submitting_batches || 0),

        batches: Array.isArray(data.batches) ? data.batches : [],

        failedBatchErrors: Array.isArray(data.failed_batch_errors) ? data.failed_batch_errors : [],

        hasFailures: Boolean(data.has_failures) || Number(data.failed_batches || 0) > 0,

    };



    APP_STATE.simpleStoryboardBatchState = state;

    return state;

}



function buildSimpleStoryboardGeneratingBanner(batchState) {

    if (!batchState || batchState.totalBatches <= 0) {

        return '<div class="storyboard-generating-banner">AI正在生成简单分镜，请稍后...</div>';

    }



    return `

        <div class="storyboard-generating-banner">

            简单分镜生成中：共 ${batchState.totalBatches} 个批次，已完成 ${batchState.completedBatches} 个${batchState.submittingBatches > 0 ? `，进行中 ${batchState.submittingBatches} 个` : ''}

        </div>

    `;

}



function buildSimpleStoryboardErrorBanner(batchState, fallbackError = '') {

    const errors = batchState?.failedBatchErrors || [];

    const firstError = errors[0];

    const summaryText = firstError

        ? `Batch ${firstError.batch_index} 失败：${firstError.message || '未知错误'}`

        : (fallbackError || '简单分镜生成失败');

    return `

        <div class="storyboard-error-banner" onclick="openSimpleStoryboardErrorModal()" style="cursor: pointer;">

            ${escapeHtml(summaryText)}${errors.length > 1 ? `（另有 ${errors.length - 1} 个失败批次）` : ''}

        </div>

        <div class="storyboard-table-actions" style="margin-top: 10px;">

            <button class="secondary-button" onclick="regenerateSimpleStoryboard()">重新生成</button>

        </div>

    `;

}



function openSimpleStoryboardErrorModal() {

    const batchState = APP_STATE.simpleStoryboardBatchState;

    const errors = batchState?.failedBatchErrors || [];

    if (!errors.length) {

        return;

    }



    const fullText = errors.map(item => {

        const batchLabel = `Batch ${item.batch_index}`;

        const attemptText = item.last_attempt ? `尝试 ${item.last_attempt}` : '未记录尝试次数';

        const retryText = Number(item.retry_count || 0) > 0 ? ` | 已重试 ${item.retry_count} 次` : '';

        return `${batchLabel} | ${attemptText}${retryText}\n${item.message || ''}`;

    }).join('\n\n');



    showTextareaModal('失败批次详情', '完整错误信息', fullText, '');

}



async function retryFailedSimpleStoryboardBatches(event) {

    if (event?.stopPropagation) {

        event.stopPropagation();

    }



    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/simple-storyboard/retry-failed-batches`, {

            method: 'POST'

        });



        if (!response.ok) {

            const errorData = await response.json().catch(() => ({}));

            throw new Error(errorData.detail || '重试失败批次失败');

        }



        showToast('失败批次重试中...', 'info');

        await loadSimpleStoryboardStep();

    } catch (error) {

        console.error('Failed to retry simple storyboard batches:', error);

        showToast(error.message || '重试失败批次失败', 'error');

    }

}


function renderSimpleStoryboardPendingState() {

    const container = document.getElementById('creationContainer');

    setContentTightBottom(false);  // 使用表格布局，不闇€要紧贴底閮?

    const batchState = APP_STATE.simpleStoryboardBatchState || buildOptimisticSimpleStoryboardBatchState();

    stopSimpleStoryboardPolling();

    container.innerHTML = `

        <div class="storyboard-table-container">

            ${buildSimpleStoryboardGeneratingBanner(batchState)}

            <div class="empty-state">简单分镜生成中，请稍后...</div>

        </div>

    `;

}



function applySimpleStoryboardStepData(data, options = {}) {

    const requestVersion = Number(options.requestVersion || 0);

    if (requestVersion && requestVersion !== APP_STATE.simpleStoryboardLoadVersion) {

        return;

    }

    const container = document.getElementById('creationContainer');

    if (!container) {

        return;

    }

    const normalizedData = data || {};

    const { generating, error, shots, batch_size } = normalizedData;

    const batchState = getSimpleStoryboardBatchState(normalizedData);

    const hasShots = Array.isArray(shots) && shots.length > 0;

    const hasFailures = Boolean(batchState.hasFailures);

    const hasFatalErrorOnly = Boolean(error) && !hasFailures && !hasShots;

    console.log('[SimpleStoryboard][render]', {

        episodeId: APP_STATE.currentEpisode,

        requestVersion,

        generating: Boolean(generating),

        hasShots,

        shotsCount: Array.isArray(shots) ? shots.length : 0,

        batchSize: Number(batch_size || 0),

        totalBatches: Number(batchState.totalBatches || 0),

        completedBatches: Number(batchState.completedBatches || 0),

        failedBatches: Number(batchState.failedBatches || 0),

        submittingBatches: Number(batchState.submittingBatches || 0),

        hasFatalErrorOnly,

        error: String(error || '')

    });

    if (generating) {

        startSimpleStoryboardPolling();

    } else {

        stopSimpleStoryboardPolling();

    }

    if (hasFatalErrorOnly) {

        container.innerHTML = `

            <div class="storyboard-table-container">

                <div class="storyboard-error-banner">简单分镜生成失败: ${escapeHtml(error)}</div>

                <div class="storyboard-table-actions">

                    <button class="secondary-button" onclick="regenerateSimpleStoryboard()">重新生成</button>

                </div>

            </div>

        `;

        return;

    }

    if (!hasShots) {

        const banners = [

            generating ? buildSimpleStoryboardGeneratingBanner(batchState) : '',

            hasFailures ? buildSimpleStoryboardErrorBanner(batchState, error) : ''

        ].filter(Boolean).join('');

        container.innerHTML = `

            <div class="storyboard-table-container">

                ${banners}

                <div class="empty-state">暂无简单分镜数据</div>

                <div class="storyboard-table-actions">

                    <button class="secondary-button" onclick="regenerateSimpleStoryboard()">生成简单分镜</button>

                </div>

            </div>

        `;

        return;

    }

    renderSimpleStoryboardTable(shots, batch_size, {

        generating,

        error,

        batchState,

    });

}



async function loadSimpleStoryboardStep(options = {}) {

    const container = document.getElementById('creationContainer');

    setContentTightBottom(false);  // 使用表格布局，不闇€要紧贴底閮?

    const requestVersion = ++APP_STATE.simpleStoryboardLoadVersion;

    if (APP_STATE.simpleStoryboardSubmissionPending && !options.forceRemote) {

        if (!APP_STATE.simpleStoryboardBatchState) {

            APP_STATE.simpleStoryboardBatchState = buildOptimisticSimpleStoryboardBatchState();

        }

        renderSimpleStoryboardPendingState();

        return;

    }

    container.innerHTML = '<div class="loading">加载中...</div>';

    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/simple-storyboard`);

        const data = await response.json();

        console.log('[SimpleStoryboard][GET /simple-storyboard]', {

            episodeId: APP_STATE.currentEpisode,

            requestVersion,

            forceRemote: Boolean(options.forceRemote),

            generating: Boolean(data?.generating),

            shotsCount: Array.isArray(data?.shots) ? data.shots.length : 0,

            totalBatches: Number(data?.total_batches || 0),

            completedBatches: Number(data?.completed_batches || 0),

            failedBatches: Number(data?.failed_batches || 0),

            submittingBatches: Number(data?.submitting_batches || 0),

            error: String(data?.error || '')

        });

        applySimpleStoryboardStepData(data, { requestVersion });

    } catch (error) {

        if (requestVersion !== APP_STATE.simpleStoryboardLoadVersion) {

            return;

        }

        console.error('Failed to load simple storyboard:', error);

        container.innerHTML = '<div class="error-state">加载失败</div>';

    }

}



// 渲染绠€单分镜表格（照搬renderStoryboardTable的结构）

function renderSimpleStoryboardTable(shots, batch_size, options = {}) {

    const container = document.getElementById('creationContainer');

    const batchState = options.batchState || APP_STATE.simpleStoryboardBatchState || null;

    const generatingBanner = options.generating ? buildSimpleStoryboardGeneratingBanner(batchState) : '';

    const errorBanner = batchState?.hasFailures ? buildSimpleStoryboardErrorBanner(batchState, options.error || '') : '';



    // 生成表头

    const tableHeaders = Object.keys(SIMPLE_STORYBOARD_COLUMNS).map(key => {

        const col = SIMPLE_STORYBOARD_COLUMNS[key];

        const visibleStyle = col.visible ? '' : 'display: none;';

        const widthStyle = `width: ${col.width}px; min-width: ${col.width}px;`;

        return `

            <th class="col-${key} resizable-column" style="${visibleStyle} ${widthStyle} position: relative;" data-column="${key}">

                ${escapeHtml(col.label)}

            </th>

        `;

    }).join('');



    const hasShotsData = shots && shots.length > 0;

    const batchSummaryText = batchState && batchState.totalBatches > 0

        ? `分批字数: ${batch_size} | 共 ${shots.length} 个镜头 | 批次 ${batchState.completedBatches}/${batchState.totalBatches}`

        : `分批字数: ${batch_size} | 共 ${shots.length} 个镜头`;



    container.innerHTML = `

        <div class="storyboard-table-container">

            ${generatingBanner}

            ${errorBanner}

            <div class="storyboard-table-actions">

                <span style="font-size: 13px; color: #999; margin-right: auto;">${batchSummaryText}</span>

                <button class="secondary-button" onclick="regenerateSimpleStoryboard()">重新生成</button>

                <button class="secondary-button" onclick="saveSimpleStoryboard()">保存修改</button>

            </div>

            <div class="storyboard-table-wrapper">

                <table id="simpleStoryboardTable" class="storyboard-edit-table">

                    <thead>

                        <tr>

                            ${tableHeaders}

                            <th class="col-action" style="width: 140px; min-width: 140px;">操作</th>

                        </tr>

                    </thead>

                    <tbody id="simpleStoryboardTableBody">

                    </tbody>

                </table>

                <div style="margin-top: 15px; text-align: center;">

                    <button type="button" class="secondary-button" onclick="addSimpleStoryboardTableRow()" style="padding: 10px 30px;">

                        + 添加镜头

                    </button>

                </div>

            </div>

        </div>

    `;



    // 渲染表格琛?

    if (hasShotsData) {

        renderSimpleStoryboardTableRows(shots);

    }

}



// 渲染绠€单分镜表鏍艰

function renderSimpleStoryboardTableRows(shots) {

    const tbody = document.getElementById('simpleStoryboardTableBody');

    if (!tbody) return;



    tbody.innerHTML = shots.map((shot, index) => {

        return `

            <tr data-index="${index}" class="storyboard-row">

                <td class="col-shot_number">

                    <input type="text" value="${escapeHtml(shot.shot_number || '')}" data-field="shot_number" class="table-input" disabled style="cursor: not-allowed; opacity: 0.6;" />

                </td>

                <td class="col-original_text">

                    <textarea data-field="original_text" class="table-textarea auto-resize" placeholder="原剧本段落">${escapeHtml(shot.original_text || '')}</textarea>

                </td>

                <td class="action-cell" style="width: 140px; min-width: 140px;">

                    <div style="display: flex; flex-direction: column; gap: 4px;">

                        <button type="button" onclick="insertSimpleStoryboardRowAfter(${index})" class="table-delete-btn" title="在此镜头下方添加新镜头" style="background: white; color: #000;">添加镜头</button>

                        <button type="button" onclick="deleteSimpleStoryboardRow(${index})" class="table-delete-btn" title="删除此镜头">删除</button>

                    </div>

                </td>

            </tr>

        `;

    }).join('');



    // 为所有textarea添加鑷姩调整高度功能

    setTimeout(() => {

        document.querySelectorAll('#simpleStoryboardTable .table-textarea.auto-resize').forEach(textarea => {

            autoResizeTextarea(textarea);

            textarea.addEventListener('input', function() {

                autoResizeTextarea(this);

            });

        });

    }, 0);

}



// 绠€单分镜轮询状鎬?

let simpleStoryboardPollingInterval = null;



function startSimpleStoryboardPolling() {

    if (simpleStoryboardPollingInterval) {

        clearInterval(simpleStoryboardPollingInterval);

    }



    simpleStoryboardPollingInterval = setInterval(async () => {

        await checkSimpleStoryboardStatus();

    }, SIMPLE_STORYBOARD_POLL_INTERVAL_MS);

}



function stopSimpleStoryboardPolling() {

    if (simpleStoryboardPollingInterval) {

        clearInterval(simpleStoryboardPollingInterval);

        simpleStoryboardPollingInterval = null;

    }

}



async function checkSimpleStoryboardStatus() {

    if (!APP_STATE.currentEpisode || APP_STATE.currentStep !== 1) {

        stopSimpleStoryboardPolling();

        return;

    }



    await withPollingGuard('simpleStoryboardStatus', async () => {

        try {

            const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/simple-storyboard/status`);

            if (!response.ok) return;

            const data = await response.json();

            const prevState = APP_STATE.simpleStoryboardBatchState || null;

            const prevCompleted = Number(prevState?.completedBatches || 0);

            const prevFailed = Number(prevState?.failedBatches || 0);

            const nextState = getSimpleStoryboardBatchState(data);



            if (nextState.generating) {

                if (nextState.completedBatches > prevCompleted || nextState.failedBatches > prevFailed) {

                    await loadSimpleStoryboardStep();

                }

                return;

            }



            stopSimpleStoryboardPolling();



            if (nextState.hasFailures || data.error) {

                if (nextState.failedBatches > prevFailed) {

                    showToast('简单分镜有失败批次，请处理后再进入下一步', 'error');

                }

                await loadSimpleStoryboardStep();

                return;

            }



            if (nextState.completedBatches > prevCompleted || (data.shots_count || 0) > 0) {

                showToast('简单分镜生成完成！', 'success');

                await loadSimpleStoryboardStep();

            }

        } catch (error) {

            console.error('Failed to check simple storyboard status:', error);

        }

    });

}



// 重新生成绠€单分闀?

async function regenerateSimpleStoryboard() {

    const confirmed = await showConfirmModal(

        '重新生成会清空当前简单分镜数据，是否确认？',

        '确认'

    );

    if (!confirmed) return;



    await generateSimpleStoryboardAndProceed();

}



// 保存绠€单分镜修鏀?

async function saveSimpleStoryboard(silent = false) {

    const tableBody = document.getElementById('simpleStoryboardTableBody');

    if (!tableBody) return;



    const rows = tableBody.querySelectorAll('tr[data-index]');

    const shots = [];



    rows.forEach((row, index) => {

        const textarea = row.querySelector('textarea[data-field="original_text"]');

        const originalText = textarea ? textarea.value.trim() : '';



        shots.push({

            shot_number: index + 1,

            original_text: originalText

        });

    });



    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/simple-storyboard`, {

            method: 'PUT',

            body: JSON.stringify({ shots })

        });



        if (response.ok) {

            if (!silent) {

                showToast('简单分镜已保存', 'success');

                // 重新加载以更新镜鍙?

                await loadSimpleStoryboardStep();

            }

        } else {

            if (!silent) {

                showToast('保存失败', 'error');

            }

        }

    } catch (error) {

        console.error('Failed to save simple storyboard:', error);

        if (!silent) {

            showToast('保存失败', 'error');

        }

    }

}



// 在指定镜头后插入新镜头（绠€单分镜）

function insertSimpleStoryboardRowAfter(index) {

    const tableBody = document.getElementById('simpleStoryboardTableBody');

    if (!tableBody) return;



    const rows = tableBody.querySelectorAll('tr[data-index]');

    const targetRow = rows[index];



    if (!targetRow) return;



    // 创建鏂拌

    const newRow = document.createElement('tr');

    newRow.setAttribute('data-index', index + 1);

    newRow.className = 'storyboard-row';

    newRow.innerHTML = `

        <td class="col-shot_number">

            <input type="text" value="${index + 2}" data-field="shot_number" class="table-input" disabled style="cursor: not-allowed; opacity: 0.6;" />

        </td>

        <td class="col-original_text">

            <textarea data-field="original_text" class="table-textarea auto-resize" placeholder="原剧本段落"></textarea>

        </td>

        <td class="action-cell" style="width: 140px; min-width: 140px;">

            <div style="display: flex; flex-direction: column; gap: 4px;">

                <button type="button" onclick="insertSimpleStoryboardRowAfter(${index + 1})" class="table-delete-btn" title="在此镜头下方添加新镜头" style="background: white; color: #000;">添加镜头</button>

                <button type="button" onclick="deleteSimpleStoryboardRow(${index + 1})" class="table-delete-btn" title="删除此镜头">删除</button>

            </div>

        </td>

    `;



    // 插入到目鏍囪后面

    targetRow.after(newRow);



    // 重新计算鎵€鏈夎的索寮?

    const allRows = tableBody.querySelectorAll('tr[data-index]');

    allRows.forEach((row, idx) => {

        row.setAttribute('data-index', idx);

        const shotNumberInput = row.querySelector('input[data-field="shot_number"]');

        if (shotNumberInput) {

            shotNumberInput.value = idx + 1;

        }



        // 更新按钮的onclick

        const addBtn = row.querySelector('button[onclick^="insertSimpleStoryboardRowAfter"]');

        const delBtn = row.querySelector('button[onclick^="deleteSimpleStoryboardRow"]');

        if (addBtn) addBtn.setAttribute('onclick', `insertSimpleStoryboardRowAfter(${idx})`);

        if (delBtn) delBtn.setAttribute('onclick', `deleteSimpleStoryboardRow(${idx})`);

    });



    // 为新textarea添加鑷姩调整高度

    const newTextarea = newRow.querySelector('.table-textarea.auto-resize');

    if (newTextarea) {

        autoResizeTextarea(newTextarea);

        newTextarea.addEventListener('input', function() {

            autoResizeTextarea(this);

        });

        newTextarea.focus();

    }



    // 鑷姩保存（静默模式）

    saveSimpleStoryboard(true);

}



// 在简单分镜表底部添加新镜澶?

function addSimpleStoryboardTableRow() {

    const tableBody = document.getElementById('simpleStoryboardTableBody');

    if (!tableBody) return;



    const rows = tableBody.querySelectorAll('tr[data-index]');

    const newIndex = rows.length;

    const newShotNumber = newIndex + 1;



    // 创建鏂拌

    const newRow = document.createElement('tr');

    newRow.setAttribute('data-index', newIndex);

    newRow.className = 'storyboard-row';

    newRow.innerHTML = `

        <td class="col-shot_number">

            <input type="text" value="${newShotNumber}" data-field="shot_number" class="table-input" disabled style="cursor: not-allowed; opacity: 0.6;" />

        </td>

        <td class="col-original_text">

            <textarea data-field="original_text" class="table-textarea auto-resize" placeholder="原剧本段落"></textarea>

        </td>

        <td class="action-cell" style="width: 140px; min-width: 140px;">

            <div style="display: flex; flex-direction: column; gap: 4px;">

                <button type="button" onclick="insertSimpleStoryboardRowAfter(${newIndex})" class="table-delete-btn" title="在此镜头下方添加新镜头" style="background: white; color: #000;">添加镜头</button>

                <button type="button" onclick="deleteSimpleStoryboardRow(${newIndex})" class="table-delete-btn" title="删除此镜头">删除</button>

            </div>

        </td>

    `;



    tableBody.appendChild(newRow);



    // 为新textarea添加鑷姩调整高度

    const newTextarea = newRow.querySelector('.table-textarea.auto-resize');

    if (newTextarea) {

        autoResizeTextarea(newTextarea);

        newTextarea.addEventListener('input', function() {

            autoResizeTextarea(this);

        });

        newTextarea.focus();

    }



    // 鑷姩保存（静默模式）

    saveSimpleStoryboard(true);

}



// 从简单分镜中删除琛?

async function deleteSimpleStoryboardRow(index) {

    const confirmed = await showConfirmModal('确认删除这个镜头吗？', '删除');

    if (!confirmed) return;



    const tableBody = document.getElementById('simpleStoryboardTableBody');

    if (!tableBody) return;



    const row = tableBody.querySelector(`tr[data-index="${index}"]`);

    if (row) {

        row.remove();



        // 鑷姩保存（静默模式）

        await saveSimpleStoryboard(true);

    }

}



// 分镜表列配置（默认全部显示）

const STORYBOARD_COLUMNS = {

    shot_number: { label: '镜号', visible: true, required: true, width: 80 },

    subjects: { label: '角色/场景/道具', visible: true, required: true, width: 125 },

    original_text: { label: '原剧本段落', visible: true, width: 250 },

    dialogue: { label: '对白', visible: true, width: 200 }

};



// 从localStorage加载列显示配缃?

function loadColumnVisibility() {

    const saved = localStorage.getItem('storyboard_column_visibility');

    if (saved) {

        try {

            const config = JSON.parse(saved);

            Object.keys(config).forEach(key => {

                if (STORYBOARD_COLUMNS[key]) {

                    STORYBOARD_COLUMNS[key].visible = config[key].visible !== undefined ? config[key].visible : STORYBOARD_COLUMNS[key].visible;

                    STORYBOARD_COLUMNS[key].width = config[key].width || STORYBOARD_COLUMNS[key].width;

                }

            });

        } catch (error) {

            console.error('Failed to load column visibility:', error);

        }

    }

}



// 保存列显示配缃埌localStorage

function saveColumnVisibility() {

    const config = {};

    Object.keys(STORYBOARD_COLUMNS).forEach(key => {

        config[key] = {

            visible: STORYBOARD_COLUMNS[key].visible,

            width: STORYBOARD_COLUMNS[key].width

        };

    });

    localStorage.setItem('storyboard_column_visibility', JSON.stringify(config));

}



// 绗?步：分镜表界闈紙鏂板锛?

// 全局鐘舵€?

let storyboardData = [];  // 分镜表数鎹紙shots锛?

let storyboardSubjects = [];  // 主体数据（subjects锛?



async function loadStoryboardTableStep() {

    const container = document.getElementById('creationContainer');

    setContentTightBottom(false);



    container.innerHTML = '<div class="loading">加载中...</div>';



    // 加载列显示配缃?

    loadColumnVisibility();



    try {

        // 获取分镜表数鎹?

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/storyboard`);

        const data = await response.json();



        const generating = data.generating || false;

        const shots = data.shots || [];

        const subjects = data.subjects || [];  // 鉁?获取subjects

        const error = data.error || '';  // 鉁?获取閿欒信息



        // 保存到全灞€变量

        storyboardSubjects = subjects;



        // 如果正在生成，启动轮璇?

        if (generating) {

            startStoryboardPolling();

        }



        // 渲染分镜琛?

        renderStoryboardTable(shots, generating, error);



    } catch (error) {

        console.error('Failed to load storyboard:', error);

        container.innerHTML = '<div class="empty-state">加载失败</div>';

    }

}



// 渲染分镜琛紙鍙紪辑表格）

function renderStoryboardTable(shots, generating, error = '') {

    const container = document.getElementById('creationContainer');



    // 显示生成涓垨閿欒banner

    let statusBanner = '';

    if (generating) {

        statusBanner = '<div class="storyboard-generating-banner">AI正在生成分镜表，请稍后...（可继续编辑）</div>';

    } else if (error) {

        statusBanner = `<div class="storyboard-error-banner">分镜表生成失败: ${escapeHtml(error)}</div>`;

    }



    const hasShotsData = shots && shots.length > 0;

    const regenerateBtn = ''; // 不需要重新生成按閽?



    // 生成表头（带宽度和拖拽手柄）

    const tableHeaders = Object.keys(STORYBOARD_COLUMNS).map(key => {

        const col = STORYBOARD_COLUMNS[key];

        const visibleStyle = col.visible ? '' : 'display: none;';

        const widthStyle = `width: ${col.width}px; min-width: ${col.width}px;`;

        return `

            <th class="col-${key} resizable-column" style="${visibleStyle} ${widthStyle} position: relative;" data-column="${key}">

                ${escapeHtml(col.label)}

                <div class="column-resizer" data-column="${key}" style="position: absolute; right: 0; top: 0; bottom: 0; width: 5px; cursor: col-resize; background: transparent; z-index: 10;"

                     onmousedown="startColumnResize(event, '${key}')"></div>

            </th>

        `;

    }).join('');



    // 计算鍙列数

    const visibleColumnCount = Object.values(STORYBOARD_COLUMNS).filter(c => c.visible).length + 1; // +1 for 操作鍒?



    container.innerHTML = `

        <div class="storyboard-table-container">

            ${statusBanner}

            <div class="storyboard-table-actions" style="display: flex; justify-content: space-between; align-items: center;">

                <div>

                    ${regenerateBtn}

                    <button class="secondary-button" onclick="showColumnSettingsModal()">列显示设置</button>

                    <button class="secondary-button" onclick="exportStoryboard()">导出分镜表</button>

                    <button class="secondary-button" onclick="triggerImportStoryboard()">导入分镜表</button>

                </div>

                <div>

                    <button class="secondary-button" onclick="saveStoryboardData()">保存/更新分镜表</button>

                </div>

                <input type="file" id="storyboardImportInput" accept=".xls,.xlsx" style="display:none" onchange="handleStoryboardImport(event)" />

            </div>

            <div class="storyboard-table-wrapper">

                <table id="storyboardTable" class="storyboard-edit-table">

                    <thead>

                        <tr>

                            ${tableHeaders}

                            <th class="col-action" style="width: 80px; min-width: 80px;">操作</th>

                        </tr>

                    </thead>

                    <tbody id="storyboardTableBody">

                        ${hasShotsData ? '' : `<tr><td colspan="${visibleColumnCount}" class="empty-row">暂无分镜表数据</td></tr>`}

                    </tbody>

                </table>

                <div style="margin-top: 15px; text-align: center;">

                    <button type="button" class="secondary-button" onclick="addStoryboardTableRow()" style="padding: 10px 30px;">

                        + 添加镜头

                    </button>

                </div>

            </div>

        </div>

    `;



    // 如果有数鎹紝渲染

    if (hasShotsData) {

        storyboardData = shots;



        // 鉁?为没鏈?stable_id 的镜头生鎴?stable_id（兼容旧数据锛?

        storyboardData.forEach(shot => {

            if (!shot.stable_id) {

                shot.stable_id = generateUUID();

            }

        });



        renderStoryboardTableRows();

    }

}



// 鍒楀调整相关变量

let resizingColumn = null;

let resizeStartX = 0;

let resizeStartWidth = 0;



// 寮€始调整列瀹?

function startColumnResize(event, columnKey) {

    event.preventDefault();

    event.stopPropagation();



    resizingColumn = columnKey;

    resizeStartX = event.clientX;

    resizeStartWidth = STORYBOARD_COLUMNS[columnKey].width;



    // 添加全局事件监听

    document.addEventListener('mousemove', handleColumnResize);

    document.addEventListener('mouseup', stopColumnResize);



    // 添加不可选择样式，防止拖拽时选中文本

    document.body.style.userSelect = 'none';

    document.body.style.cursor = 'col-resize';

}



// 处理鍒楀调整

function handleColumnResize(event) {

    if (!resizingColumn) return;



    const deltaX = event.clientX - resizeStartX;

    const newWidth = Math.max(50, resizeStartWidth + deltaX); // 鏈€灏忓搴?0px



    // 更新配置

    STORYBOARD_COLUMNS[resizingColumn].width = newWidth;



    // 更新表头宽度

    const th = document.querySelector(`th.col-${resizingColumn}`);

    if (th) {

        th.style.width = `${newWidth}px`;

        th.style.minWidth = `${newWidth}px`;

    }



    // 更新鎵€鏈夎鐨勫应列宽度

    const tds = document.querySelectorAll(`td.col-${resizingColumn}`);

    tds.forEach(td => {

        td.style.width = `${newWidth}px`;

        td.style.minWidth = `${newWidth}px`;

    });

}



// 鍋滄调整鍒楀

function stopColumnResize() {

    if (resizingColumn) {

        // 保存鍒楀配置

        saveColumnVisibility();

        resizingColumn = null;

    }



    // 移除全局事件监听

    document.removeEventListener('mousemove', handleColumnResize);

    document.removeEventListener('mouseup', stopColumnResize);



    // 鎭㈠样式

    document.body.style.userSelect = '';

    document.body.style.cursor = '';

}



// 显示鍒楄缃ā鎬佹

function showColumnSettingsModal() {

    const modal = document.getElementById('columnSettingsModal');

    if (!modal) {

        // 创建妯℃€佹

        const modalHtml = `

            <div id="columnSettingsModal" class="modal" style="display: flex; align-items: center; justify-content: center;">

                <div class="modal-content" style="background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 25px; width: 400px; max-width: 90vw;">

                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">

                        <h3 style="margin: 0; color: #fff; font-size: 16px;">列显示设置</h3>

                        <button onclick="closeColumnSettingsModal()" style="background: transparent; border: none; color: #888; font-size: 24px; cursor: pointer; padding: 0; line-height: 1;">&times;</button>

                    </div>

                    <div id="columnSettingsList" style="max-height: 400px; overflow-y: auto;">

                        <!-- 鍔ㄦ€佺敓鎴?-->

                    </div>

                    <div style="display: flex; gap: 10px; margin-top: 20px; padding-top: 15px; border-top: 1px solid #333;">

                        <button class="primary-button" onclick="applyColumnSettings()" style="flex: 1;">应用</button>

                        <button class="secondary-button" onclick="closeColumnSettingsModal()" style="flex: 1;">取消</button>

                    </div>

                </div>

            </div>

        `;

        document.body.insertAdjacentHTML('beforeend', modalHtml);

    }



    // 濉厖鍒楄缃垪琛?

    const listContainer = document.getElementById('columnSettingsList');

    listContainer.innerHTML = Object.keys(STORYBOARD_COLUMNS).map(key => {

        const col = STORYBOARD_COLUMNS[key];

        const disabled = col.required ? 'disabled' : '';

        const disabledStyle = col.required ? 'opacity: 0.5; cursor: not-allowed;' : '';

        return `

            <label style="display: flex; align-items: center; gap: 10px; padding: 10px; cursor: ${col.required ? 'not-allowed' : 'pointer'}; border-radius: 4px; transition: background 0.2s; ${disabledStyle}"

                   ${col.required ? '' : 'onmouseover="this.style.background=\'#0a0a0a\'" onmouseout="this.style.background=\'transparent\'"'}>

                <input type="checkbox"

                       class="column-visibility-checkbox"

                       data-column="${key}"

                       ${col.visible ? 'checked' : ''}

                       ${disabled}

                       style="width: 18px; height: 18px; cursor: ${col.required ? 'not-allowed' : 'pointer'};">

                <span style="color: #fff; font-size: 14px;">${escapeHtml(col.label)}</span>

                ${col.required ? '<span style="color: #888; font-size: 12px; margin-left: auto;">(必需)</span>' : ''}

            </label>

        `;

    }).join('');



    // 显示妯℃€佹

    document.getElementById('columnSettingsModal').style.display = 'flex';

}



// 关闭鍒楄缃ā鎬佹

function closeColumnSettingsModal() {

    const modal = document.getElementById('columnSettingsModal');

    if (modal) {

        modal.style.display = 'none';

    }

}



// 应用鍒楄缃?

function applyColumnSettings() {

    // 读取鎵€鏈夊閫夋鐘舵€?

    document.querySelectorAll('.column-visibility-checkbox').forEach(checkbox => {

        const columnKey = checkbox.dataset.column;

        if (STORYBOARD_COLUMNS[columnKey] && !STORYBOARD_COLUMNS[columnKey].required) {

            STORYBOARD_COLUMNS[columnKey].visible = checkbox.checked;

        }

    });



    // 保存到localStorage

    saveColumnVisibility();



    // 关闭妯℃€佹

    closeColumnSettingsModal();



    // 重新渲染表格

    const hasShotsData = storyboardData && storyboardData.length > 0;

    renderStoryboardTable(storyboardData, false, '');

}



// 在渲染前先同姝OM数据到storyboardData（防止数鎹涪失）

function syncDOMToStoryboardData() {

    const rows = document.querySelectorAll('#storyboardTableBody tr');



    rows.forEach((row) => {

        const index = parseInt(row.getAttribute('data-index'));

        if (isNaN(index) || !storyboardData[index]) return;



        // 鍚屾文本瀛楁

        const originalTextArea = row.querySelector('[data-field="original_text"]');

        const dialogueTextArea = row.querySelector('[data-field="dialogue"]');



        if (originalTextArea) {

            storyboardData[index].original_text = originalTextArea.value;

        }

        if (dialogueTextArea) {

            storyboardData[index].dialogue = dialogueTextArea.value;

        }



        // 鍚屾主体数据

        const characterRows = row.querySelectorAll('.character-row');

        const subjects = [];



        characterRows.forEach((charRow) => {

            const nameInput = charRow.querySelector('[data-field="char_name"]');

            const typeSelect = charRow.querySelector('[data-field="char_type"]');



            if (nameInput && typeSelect) {

                subjects.push({

                    name: nameInput.value.trim(),

                    type: typeSelect.value

                });

            }

        });



        if (subjects.length > 0 || storyboardData[index].subjects) {

            storyboardData[index].subjects = subjects;

        }

    });



    console.log('[鍚屾数据] DOM数据已同步到storyboardData');

}



// 渲染分镜琛ㄨ

function renderStoryboardTableRows() {

    const tbody = document.getElementById('storyboardTableBody');

    if (!tbody) return;



    console.log('[渲染分镜表] 寮€濮?- 数据行数:', storyboardData.length);



    tbody.innerHTML = storyboardData.map((shot, index) => {

        // 鉁?在分镜表编辑界面，直接使鐢?shot.subjects 数组（允许自由编辑）

        let subjects = [];



        // 优先使用 shot.subjects 数组（用户在分镜表界面添加的锛?

        if (Array.isArray(shot.subjects) && shot.subjects.length > 0) {

            subjects = shot.subjects;

            console.log(`[渲染分镜表] 镜头${index} - 使用 shot.subjects:`, subjects);

        }

        // 鍥為€€：尝试从 selected_card_ids 读取（兼容故事板界面锛?

        else {

            try {

                const selectedIds = JSON.parse(shot.selected_card_ids || '[]');



                if (selectedIds.length > 0 && storyboardSubjects.length > 0) {

                    // 浠?storyboardSubjects 涓煡鎵惧应的主体

                    subjects = selectedIds.map(id => {

                        const card = storyboardSubjects.find(s => s.id === id);

                        if (card) {

                            return {

                                name: card.name,

                                type: card.card_type

                            };

                        }

                        return null;

                    }).filter(s => s !== null);

                    console.log(`[渲染分镜表] 镜头${index} - 使用 selected_card_ids:`, subjects);

                }

            } catch (e) {

                console.error(`[渲染分镜表] 镜头${index} - 解析 selected_card_ids 失败:`, e);

            }



            // 鏈€后回閫€到旧数据结构

            if (subjects.length === 0) {

                if (Array.isArray(shot.characters)) {

                    subjects = shot.characters;

                    console.log(`[渲染分镜表] 镜头${index} - 使用 shot.characters:`, subjects);

                } else if (shot.characters && typeof shot.characters === 'object') {

                    subjects = [shot.characters];

                    console.log(`[渲染分镜表] 镜头${index} - 使用单个 shot.characters:`, subjects);

                }

            }

        }



        console.log(`[渲染分镜表] 镜头${index} - 鏈€终主体列琛?`, subjects);



        // 生成主体列表的HTML（每涓富体一行）

        const subjectsHtml = subjects.map((subj, subjIndex) => `

            <div class="character-row" style="display: flex; gap: 4px; margin-bottom: 8px; align-items: center;">

                <input type="text"

                       value="${escapeHtml(subj.name || '')}"

                       data-char-index="${subjIndex}"

                       data-field="char_name"

                       placeholder="主体名称"

                       class="table-input"

                       style="flex: 1; min-width: 0; font-size: 14px; padding: 6px 8px;">

                <select data-char-index="${subjIndex}"

                        data-field="char_type"

                        class="table-select"

                        style="width: 70px; font-size: 14px; padding: 6px 6px;">

                    <option value="角色" ${subj.type === '角色' ? 'selected' : ''}>角色</option>

                    <option value="场景" ${subj.type === '场景' ? 'selected' : ''}>场景</option>

                    <option value="道具" ${subj.type === '道具' ? 'selected' : ''}>道具</option>

                </select>

                <button type="button" onclick="removeCharacterFromTableShot(${index}, ${subjIndex})"

                        class="table-char-delete-btn"

                        title="删除此主体"

                        style="background: #555; color: white; border: none; padding: 6px 8px; border-radius: 2px; cursor: pointer; font-size: 12px;">

                    ×

                </button>

            </div>

        `).join('');



        // 生成单元格的辅助函数

        const createCell = (columnKey, content) => {

            const col = STORYBOARD_COLUMNS[columnKey];

            const visibleStyle = col.visible ? '' : 'display: none;';

            const widthStyle = `width: ${col.width}px; min-width: ${col.width}px;`;

            return `<td class="col-${columnKey}" style="${visibleStyle} ${widthStyle}">${content}</td>`;

        };



        const subjectsCell = createCell('subjects', `

            <div class="characters-container" data-shot-index="${index}">

                ${subjectsHtml || '<div style="color: #666; font-size: 13px; padding: 6px;">暂无主体</div>'}

                <button type="button" onclick="addCharacterToTableShot(${index})"

                        class="table-add-char-btn"

                        style="background: #333; color: white; border: none; padding: 6px 8px; border-radius: 2px; cursor: pointer; font-size: 13px; margin-top: 4px; width: 100%;">

                    + 添加主体

                </button>

            </div>

        `);



        return `

            <tr data-index="${index}" class="storyboard-row">

                ${createCell('shot_number', `<input type="text" value="${escapeHtml(shot.shot_number || '')}" data-field="shot_number" class="table-input" disabled style="cursor: not-allowed; opacity: 0.6;" />`)}

                ${subjectsCell}

                ${createCell('original_text', `<textarea data-field="original_text" class="table-textarea auto-resize" placeholder="原剧本段落">${escapeHtml(shot.original_text || shot.script_excerpt || '')}</textarea>`)}

                ${createCell('dialogue', `<textarea data-field="dialogue" class="table-textarea auto-resize" placeholder="对白内容">${escapeHtml(shot.dialogue || '')}</textarea>`)}

                <td class="action-cell" style="width: 140px; min-width: 140px;">

                    <div style="display: flex; flex-direction: column; gap: 4px;">

                        <button type="button" onclick="insertStoryboardRowAfter(${index})" class="table-delete-btn" title="在此镜头下方添加新镜头" style="background: white; color: #000;">添加镜头</button>

                        <button type="button" onclick="deleteStoryboardTableRow(${index})" class="table-delete-btn" title="删除此镜头">删除</button>

                    </div>

                </td>

            </tr>

        `;

    }).join('');



    // 为所有textarea添加鑷姩调整高度功能（排除分镜提示词锛?

    setTimeout(() => {

        document.querySelectorAll('.table-textarea.auto-resize').forEach(textarea => {

            autoResizeTextarea(textarea);

            textarea.addEventListener('input', function() {

                autoResizeTextarea(this);

            });

        });

    }, 0);

}



// 鑷姩调整textarea高度

function autoResizeTextarea(textarea) {

    if (!textarea) return;

    textarea.style.height = 'auto';

    textarea.style.height = textarea.scrollHeight + 'px';

}



// 杞鐘舵€侊細妫€查分镜表鏄惁生成完成

let storyboardPollingInterval = null;



function startStoryboardPolling() {

    if (storyboardPollingInterval) {

        clearInterval(storyboardPollingInterval);

    }



    storyboardPollingInterval = setInterval(async () => {

        await checkStoryboardStatus();

    }, DETAILED_STORYBOARD_POLL_INTERVAL_MS);

}



function stopStoryboardPolling() {

    if (storyboardPollingInterval) {

        clearInterval(storyboardPollingInterval);

        storyboardPollingInterval = null;

    }

}



async function checkStoryboardStatus() {

    if (!APP_STATE.currentEpisode || APP_STATE.currentStep !== 2) {

        stopStoryboardPolling();

        return;

    }



    await withPollingGuard('detailedStoryboardStatus', async () => {

        try {

            const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/storyboard/status`);

            if (!response.ok) return;

            const data = await response.json();



            // 妫€查是否有閿欒

            if (data.error) {

                // 生成失败

                stopStoryboardPolling();

                const fullResponse = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/storyboard`);

                const fullData = await fullResponse.json();

                storyboardSubjects = fullData.subjects || [];

                showToast('分镜表生成失败', 'error');

                renderStoryboardTable(fullData.shots || [], false, data.error);

                return;

            }



            if (!data.generating && (data.shots_count || 0) > 0) {

                // 生成完成

                stopStoryboardPolling();

                const fullResponse = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/storyboard`);

                const fullData = await fullResponse.json();



                // 鉁?保存subjects数据

                storyboardSubjects = fullData.subjects || [];



                showToast('分镜表生成完成！', 'success');

                renderStoryboardTable(fullData.shots || [], false, '');

            }

        } catch (error) {

            console.error('Failed to check storyboard status:', error);

        }

    });

}



// 重新生成分镜琛?

async function regenerateStoryboard() {

    const confirmed = await showConfirmModal('确定要重新生成分镜表吗？当前的编辑内容将被覆盖。', '重新生成');

    if (!confirmed) return;



    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/analyze-storyboard`, {

            method: 'POST'

        });



        if (response.ok) {

            showToast('分镜表生成任务已启动', 'info');

            startStoryboardPolling();

            renderStoryboardTable([], true, '');  // 显示"生成涓?鐘舵€?

        }

    } catch (error) {

        console.error('Failed to regenerate storyboard:', error);

        showToast('生成失败', 'error');

    }

}



// 保存分镜表数鎹?

async function saveStoryboardData(silent = false) {

    if (!silent) {

        console.log('[保存分镜表] 寮€始保瀛?');

    }



    const shots = collectStoryboardTableData();



    if (!silent) {

        console.log('[保存分镜表] 收集到的数据:', shots);

    }



    // 鉁?从镜头数鎹腑收集实际使用的主体（去重锛?

    const subjectsMap = new Map(); // 使用 Map 去重，key 鏄?name+type 的组鍚?



    shots.forEach(shot => {

        const shotSubjects = shot.subjects || [];

        shotSubjects.forEach(subj => {

            const name = (subj.name || '').trim();

            const type = (subj.type || '角色').trim();

            if (!name) return; // 跳过空名瀛?



            const key = `${name}::${type}`;

            if (!subjectsMap.has(key)) {

                // 尝试从旧鐨?subjects 列表涓壘到这涓富体的棰濆信息锛堝 ai_prompt锛?

                const oldSubject = storyboardSubjects.find(s =>

                    s.name === name && s.type === type

                );



                subjectsMap.set(key, {

                    name: name,

                    type: type,

                    alias: oldSubject?.alias || subj.alias || '',

                    ai_prompt: oldSubject?.ai_prompt || subj.ai_prompt || '',

                    role_personality: oldSubject?.role_personality || oldSubject?.role_personality_en || subj.role_personality || subj.role_personality_en || ''

                });

            }

        });

    });



    // 杞崲为数缁?

    const subjects = Array.from(subjectsMap.values());



    if (!silent) {

        console.log('[保存分镜表] 收集到的主体列表:', subjects);

    }



    // 鉁?直接保存，不再显示确认弹绐?



    // 鐪熸保存

    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/storyboard`, {

            method: 'PUT',

            body: JSON.stringify({

                shots: shots,

                subjects: subjects  // 鉁?使用从镜头中收集的主浣?

            })

        });



        if (response.ok) {

            // 更新全局变量

            storyboardSubjects = subjects;



            if (!silent) {

                showToast('分镜表已保存', 'success');

                console.log('[保存分镜表] 保存成功');

            }

        } else {

            if (!silent) {

                showToast('保存失败', 'error');

            }

            console.error('[保存分镜表] 保存失败 - HTTP鐘舵€?', response.status);

        }

    } catch (error) {

        console.error('[保存分镜表] 保存失败 - 异常:', error);

        if (!silent) {

            showToast('保存失败', 'error');

        }

    }

}



// 收集表格数据（主分镜琛級

function collectStoryboardTableData() {

    const rows = document.querySelectorAll('#storyboardTableBody tr');

    const collected = [];



    rows.forEach((row) => {

        const shotNumber = row.querySelector('[data-field="shot_number"]')?.value.trim();

        if (!shotNumber) return;  // 跳过绌鸿



        // 获取鍘熷索引

        const index = parseInt(row.getAttribute('data-index'));

        const originalShot = storyboardData[index] || {};

        const originalSubjects = Array.isArray(originalShot.subjects) ? originalShot.subjects :

                                 Array.isArray(originalShot.characters) ? originalShot.characters : [];



        // 收集该镜头的鎵€有主浣?

        const characterRows = row.querySelectorAll('.character-row');

        const subjects = [];



        characterRows.forEach((charRow, charIndex) => {

            const nameInput = charRow.querySelector('[data-field="char_name"]');

            const typeSelect = charRow.querySelector('[data-field="char_type"]');



            if (nameInput && typeSelect) {

                const name = nameInput.value.trim();

                const type = typeSelect.value;



                subjects.push({

                    name: name,

                    type: type

                });

            }

        });



        const shot = {

            id: originalShot.id || null,  // 鉁?包含数据库ID（新镜头为null锛?

            shot_number: shotNumber,

            stable_id: originalShot.stable_id,  // 保留stable_id用于变体分组

            subjects: subjects,

            original_text: row.querySelector('[data-field="original_text"]')?.value.trim() || '',

            dialogue_text: row.querySelector('[data-field="dialogue"]')?.value.trim() || '',  // 表格涓殑台词瀛楁（重命名避免冲突锛?

            // 鉁?保留配音相关瀛楁（从鍘熷数据涓級

            voice_type: originalShot.voice_type || null,

            narration: originalShot.narration || null,

            dialogue: originalShot.dialogue_array || originalShot.dialogue || []  // 优先从dialogue_array读取

        };



        collected.push(shot);

    });



    return collected;

}



// ==================== 导出分镜琛?====================

async function exportStoryboard() {

    if (!APP_STATE.currentEpisode) {

        showToast('请先选择片段', 'error');

        return;

    }



    try {

        const url = `/api/episodes/${APP_STATE.currentEpisode}/export-storyboard`;

        const response = await apiRequest(url);



        if (!response.ok) {

            const error = await response.json();

            showToast(error.detail || '导出失败', 'error');

            return;

        }



        // 获取文件鍚?

        const contentDisposition = response.headers.get('content-disposition');

        let filename = 'storyboard.xlsx';

        if (contentDisposition) {

            const filenameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);

            if (filenameMatch && filenameMatch[1]) {

                filename = filenameMatch[1].replace(/['"]/g, '');

                // 解码URL编码的文件名

                filename = decodeURIComponent(filename);

            }

        }



        // 下载文件

        const blob = await response.blob();

        const downloadUrl = window.URL.createObjectURL(blob);

        const a = document.createElement('a');

        a.href = downloadUrl;

        a.download = filename;

        document.body.appendChild(a);

        a.click();

        document.body.removeChild(a);

        window.URL.revokeObjectURL(downloadUrl);



        showToast('分镜表已导出', 'success');

    } catch (error) {

        console.error('Export failed:', error);

        showToast('导出失败', 'error');

    }

}



// ==================== 导入分镜琛?====================

function triggerImportStoryboard() {

    const input = document.getElementById('storyboardImportInput');

    if (input) {

        input.value = '';  // 清空，允许重澶嶉€夋嫨同一文件

        input.click();

    }

}



async function handleStoryboardImport(event) {

    const file = event.target.files[0];

    if (!file) return;



    if (!APP_STATE.currentEpisode) {

        showToast('请先选择片段', 'error');

        return;

    }



    // 鈿狅笍 纭对话妗?

    const confirmed = await showConfirmModal(

        '导入将会替换当前所有镜头数据，此操作不可撤销。\n\n确定要继续吗？',

        '确认导入分镜表'

    );



    if (!confirmed) {

        return;

    }



    try {

        const formData = new FormData();

        formData.append('file', file);



        const token = localStorage.getItem('authToken');

        const response = await fetch(`/api/episodes/${APP_STATE.currentEpisode}/import-storyboard`, {

            method: 'POST',

            headers: {

                'Authorization': `Bearer ${token}`

            },

            body: formData

        });



        const result = await response.json();



        if (!response.ok) {

            showToast(result.detail || '导入失败', 'error');

            return;

        }



        // 成功

        showToast(`导入成功！\n导入 ${result.imported_shots} 个镜头\n删除 ${result.deleted_shots} 个旧镜头\n创建 ${result.created_subjects} 个主体`, 'success');



        // 刷新分镜表数鎹?

        await loadStoryboardTableStep();

    } catch (error) {

        console.error('Import failed:', error);

        showToast('导入失败', 'error');

    }

}



// 添加主体到镜头（主分镜表锛?

function addCharacterToTableShot(shotIndex) {

    console.log('[添加主体] 寮€濮?- shotIndex:', shotIndex);



    // 鉁?先同姝OM数据，防姝涪失用户输鍏?

    syncDOMToStoryboardData();



    if (!storyboardData[shotIndex]) {

        console.error('[添加主体] 失败 - 找不到镜头数鎹?');

        return;

    }



    // 统一使用subjects瀛楁

    if (!Array.isArray(storyboardData[shotIndex].subjects)) {

        storyboardData[shotIndex].subjects = [];

    }



    // 添加新主浣?

    const newSubject = {

        name: '',

        type: '角色'

    };

    storyboardData[shotIndex].subjects.push(newSubject);



    console.log('[添加主体] 成功 - 新主浣?', newSubject);

    console.log('[添加主体] 镜头当前主体列表:', storyboardData[shotIndex].subjects);



    renderStoryboardTableRows();



    // 鑷姩保存（静默模式）

    saveStoryboardData(true);

}



// 从镜头删除主体（主分镜表锛?

function removeCharacterFromTableShot(shotIndex, charIndex) {

    // 鉁?先同姝OM数据，防姝涪失用户输鍏?

    syncDOMToStoryboardData();



    if (!storyboardData[shotIndex]) return;

    const subjects = storyboardData[shotIndex].subjects || storyboardData[shotIndex].characters;

    if (!Array.isArray(subjects)) return;



    showConfirmModal('确定要删除这个主体吗？', '删除主体').then(confirmed => {

        if (confirmed) {

            subjects.splice(charIndex, 1);

            renderStoryboardTableRows();



            // 鑷姩保存（静默模式）

            saveStoryboardData(true);

        }

    });

}



// 添加分镜琛ㄨ（主界面锛?

function addStoryboardTableRow() {

    // 鉁?先同姝OM数据，防姝涪失用户输鍏?

    syncDOMToStoryboardData();



    const maxNumber = Math.max(...storyboardData.map(s => parseInt(s.shot_number) || 0), 0);

    storyboardData.push({

        shot_number: String(maxNumber + 1),

        subjects: [],

        original_text: '',

        dialogue: ''

    });

    renderStoryboardTableRows();



    // 鑷姩保存（静默模式）

    saveStoryboardData(true);

}



// 删除分镜琛ㄨ

// 在指瀹氳下方插入新镜澶?

function insertStoryboardRowAfter(index) {

    // 鉁?先同姝OM数据，防姝涪失用户输鍏?

    syncDOMToStoryboardData();



    // 获取当前镜头鍙?

    const currentShotNumber = parseInt(storyboardData[index].shot_number) || (index + 1);

    const newShotNumber = currentShotNumber + 1;



    // 创建新的空白镜头

    const newShot = {

        id: null,  // 鉁?新镜头没有数鎹簱ID

        stable_id: generateUUID(),  // 鉁?为新镜头生成鍞竴鐨?stable_id（用于变体分组）

        shot_number: newShotNumber.toString(),

        characters: [],

        original_text: '',

        dialogue: ''

    };



    // 在当前位缃悗插入新镜澶?

    storyboardData.splice(index + 1, 0, newShot);



    // 更新后续鎵€有镜头的镜头号（+1锛?

    // ⚠️ 注意：只更新 shot_number，不改变 stable_id

    for (let i = index + 2; i < storyboardData.length; i++) {

        const oldNumber = parseInt(storyboardData[i].shot_number) || i;

        storyboardData[i].shot_number = (oldNumber + 1).toString();

        // stable_id 保持不变锛?

    }



    // 重新渲染表格

    renderStoryboardTableRows();



    // 鑷姩保存（静默模式）

    saveStoryboardData(true);



    showToast(`已在镜头 ${currentShotNumber} 下方添加新镜头 ${newShotNumber}`, 'success');

}



function deleteStoryboardTableRow(index) {

    // 鉁?先同姝OM数据，防姝涪失用户输鍏?

    syncDOMToStoryboardData();



    showConfirmModal('确定要删除这个镜头吗？', '删除镜头').then(confirmed => {

        if (confirmed) {

            storyboardData.splice(index, 1);

            renderStoryboardTableRows();



            // 鑷姩保存（静默模式）

            saveStoryboardData(true);

        }

    });

}



// 从分镜表创建主体和镜澶?

async function createFromStoryboard() {

    // 先保存当前编杈?

    await saveStoryboardData();



    // 直接创建，不闇€要确认弹绐?



    try {

        showToast('正在创建主体和镜头...', 'info');



        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/create-from-storyboard`, {

            method: 'POST'

        });



        if (response.ok) {

            const result = await response.json();

            showToast(`创建成功！新增 ${result.created_subjects} 个主体，${result.created_shots} 个镜头`, 'success');



            // 跳转到主体界闈紙姝ラ3锛?

            await switchStep(3);

        } else {

            const error = await response.json();

            showToast(`创建失败: ${error.detail || '未知错误'}`, 'error');

        }

    } catch (error) {

        console.error('Failed to create from storyboard:', error);

        showToast('创建失败', 'error');

    }

}



// 绗?步：主体界面（原来的绗?步）

async function loadSubjectStep() {

    const container = document.getElementById('creationContainer');

    if (!container) {

        return;

    }

    setContentTightBottom(true);



    container.innerHTML = '<div class="loading">加载中...</div>';



    try {

        // 从episode获取library_id

        const episodesResponse = await apiRequest(`/api/scripts/${APP_STATE.currentScript}/episodes`);

        const episodes = await episodesResponse.json();

        const episode = episodes.find(ep => ep.id === APP_STATE.currentEpisode);



        if (!episode) {

            throw new Error('该剧集不存在');

        }



        if (!episode.library_id) {

            throw new Error('该剧集没有关联的主体库');

        }



        APP_STATE.library = { id: episode.library_id };



        // 获取卡片（主体页包含声音卡片），并确保有默认旁白卡片

        APP_STATE.cards = await ensureSubjectCardsWithDefaultSound(episode.library_id);



        container.innerHTML = `

            <div class="subject-layout">

                <div class="subject-cards-area">

                    <div style="margin-bottom: 15px; display: flex; justify-content: flex-end; position: sticky; top: 0; background: #0a0a0a; z-index: 10; padding: 10px 0;">

                        <button class="secondary-button" onclick="batchGenerateAiPrompts()">批量生成绘画提示词</button>

                    </div>

                    <div class="subject-vertical-layout">

                        <div class="subject-section">

                            <div class="subject-column-header">

                                <h3>角色</h3>

                                <button class="column-add-button" onclick="createSubjectCardByType('角色')">+ 新建角色</button>

                            </div>

                            <div class="subject-grid-content" id="charactersColumn"></div>

                        </div>

                        <div class="subject-section">

                            <div class="subject-column-header">

                                <h3>场景</h3>

                                <button class="column-add-button" onclick="createSubjectCardByType('场景')">+ 新建场景</button>

                            </div>

                            <div class="subject-grid-content" id="scenesColumn"></div>

                        </div>

                        <div class="subject-section">

                            <div class="subject-column-header">

                                <h3>道具</h3>

                                <button class="column-add-button" onclick="createSubjectCardByType('道具')">+ 新建道具</button>

                            </div>

                            <div class="subject-grid-content" id="propsColumn"></div>

                        </div>

                        <div class="subject-section">

                            <div class="subject-column-header">

                                <h3>声音</h3>

                                <button class="column-add-button" onclick="createSubjectCardByType('声音')">+ 新建声音</button>

                            </div>

                            <div class="subject-grid-content" id="soundsColumn"></div>

                        </div>

                    </div>

                </div>

                <div class="subject-sidebar" id="subjectSidebar">

                    <div class="subject-sidebar-title">AI Prompt</div>

                    <div class="subject-sidebar-empty">选择卡片以查鐪?编辑prompt</div>

                </div>

            </div>

        `;



        renderSubjectCards();

    } catch (error) {

        console.error('Failed to load subjects:', error);

        container.innerHTML = '<div class="empty-state">加载失败</div>';

    }

}



async function loadStoryboardCards() {

    const scriptResponse = await apiRequest(`/api/scripts/${APP_STATE.currentScript}`);

    const script = await scriptResponse.json();

    APP_STATE.currentScriptInfo = script;

    APP_STATE.soraPromptStyle = script.sora_prompt_style || '';



    // 从episode获取library_id

    const episodesResponse = await apiRequest(`/api/scripts/${APP_STATE.currentScript}/episodes`);

    const episodes = await episodesResponse.json();

    const episode = episodes.find(ep => ep.id === APP_STATE.currentEpisode);



    if (!episode) {

        throw new Error('该剧集不存在');

    }



    if (!episode.library_id) {

        throw new Error('该剧集没有关联的主体库');

    }



    APP_STATE.library = { id: episode.library_id };



    APP_STATE.cards = await ensureSubjectCardsWithDefaultSound(episode.library_id);

}



async function fetchLibraryCards(libraryId, includeSound = false) {

    const query = includeSound ? '?include_sound=true' : '';

    const cardsResponse = await apiRequest(`/api/libraries/${libraryId}/cards${query}`);

    if (!cardsResponse || !cardsResponse.ok) {

        throw new Error('获取主体卡片失败');

    }

    return await cardsResponse.json();

}



async function ensureSubjectCardsWithDefaultSound(libraryId) {

    let cards = await fetchLibraryCards(libraryId, true);

    const roleNames = Array.from(new Set(

        cards

            .filter(card => card.card_type === '角色')

            .map(card => (card.name || '').trim())

            .filter(Boolean)

    ));



    const existingSoundNames = new Set(

        cards

            .filter(card => card.card_type === '声音')

            .map(card => (card.name || '').trim())

            .filter(Boolean)

    );



    const namesToCreate = [];

    if (!existingSoundNames.has('旁白')) {

        namesToCreate.push('旁白');

    }

    roleNames.forEach(name => {

        if (!existingSoundNames.has(name)) {

            namesToCreate.push(name);

        }

    });



    if (namesToCreate.length === 0) {

        return cards;

    }



    for (const name of namesToCreate) {

        const response = await apiRequest(`/api/libraries/${libraryId}/cards`, {

            method: 'POST',

            body: JSON.stringify({

                name,

                card_type: '声音'

            })

        });

        if (!response || !response.ok) {

            console.warn('Failed to auto create sound card:', name);

        }

    }



    cards = await fetchLibraryCards(libraryId, true);

    return cards;

}


function groupSubjectCardsByType(cards) {

    const cardList = Array.isArray(cards) ? cards : [];

    return {

        characters: cardList.filter(card => card.card_type === '角色'),

        scenes: cardList.filter(card => card.card_type === '场景'),

        props: cardList.filter(card => card.card_type === '道具'),

        sounds: cardList.filter(card => card.card_type === '声音'),

    };

}


function resolveSubjectCardType(cardType) {

    if (cardType === '场景') {

        return '场景';

    }

    if (cardType === '道具') {

        return '道具';

    }

    if (cardType === '声音') {

        return '声音';

    }

    return '角色';

}


function getDefaultSubjectCardName(cardType) {

    const defaultNames = {

        '角色': '未命名角色',

        '场景': '未命名场景',

        '道具': '未命名道具',

        '声音': '未命名声音'

    };

    return defaultNames[resolveSubjectCardType(cardType)] || '未命名主体';

}


function getSubjectPromptPlaceholder(cardType) {

    if (cardType === '角色') {

        return '描述角色的外貌特征（例如：25岁男性，深邃的黑色眼睛，黑色短发清爽利落...）';

    }

    if (cardType === '场景') {

        return '描述场景的外观（例如：现代风格的咖啡厅，木质家具，暖色调灯光...）';

    }

    if (cardType === '道具') {

        return '描述道具的材质、结构和关键细节（例如：青铜材质的古旧匕首，刀刃有磨损，木柄缠着发黑布条...）';

    }

    return '';

}


function getSubjectPromptStatusText(cardType) {

    if (cardType === '角色') {

        return '角色描述已生成';

    }

    if (cardType === '场景') {

        return '场景描述已生成';

    }

    if (cardType === '道具') {

        return '道具描述已生成';

    }

    return '提示词已生成';

}


// 渲染主体卡片

function renderSubjectCards() {

    // 按类型分类卡鐗?

    const { characters, scenes, props, sounds } = groupSubjectCardsByType(APP_STATE.cards);



    // 渲染角色鍒?

    const charactersColumn = document.getElementById('charactersColumn');

    if (charactersColumn) {

        charactersColumn.innerHTML = characters.length > 0

            ? characters.map(card => renderSubjectCard(card)).join('')

            : '<div class="column-empty">暂无角色卡片</div>';

    }



    // 渲染场景鍒?

    const scenesColumn = document.getElementById('scenesColumn');

    if (scenesColumn) {

        scenesColumn.innerHTML = scenes.length > 0

            ? scenes.map(card => renderSubjectCard(card)).join('')

            : '<div class="column-empty">暂无场景卡片</div>';

    }



    const propsColumn = document.getElementById('propsColumn');

    if (propsColumn) {

        propsColumn.innerHTML = props.length > 0

            ? props.map(card => renderSubjectCard(card)).join('')

            : '<div class="column-empty">暂无道具卡片</div>';

    }


    const soundsColumn = document.getElementById('soundsColumn');

    if (soundsColumn) {

        soundsColumn.innerHTML = sounds.length > 0

            ? sounds.map(card => renderSubjectCard(card)).join('')

            : '<div class="column-empty">暂无声音卡片</div>';

    }

}



function getCardPreviewImage(card) {

    if (!card) return null;



    if (card.generated_images && card.generated_images.length > 0) {

        const referenceImages = card.generated_images.filter(img => img.is_reference && img.status === 'completed');

        if (referenceImages.length > 0) {

            return referenceImages[0].image_path;

        }

    }



    return null;

}



function getCardReferenceAudio(card) {

    if (!card || !Array.isArray(card.audios) || card.audios.length === 0) {

        return null;

    }

    const reference = card.audios.find(audio => audio.is_reference);

    return reference || card.audios[0];

}



function formatAudioDurationLabel(durationSeconds) {

    const parsed = Number(durationSeconds);

    if (!Number.isFinite(parsed) || parsed <= 0) {

        return '时长未知';

    }



    const totalSeconds = Math.max(1, Math.round(parsed));

    const hours = Math.floor(totalSeconds / 3600);

    const minutes = Math.floor((totalSeconds % 3600) / 60);

    const seconds = totalSeconds % 60;



    if (hours > 0) {

        return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;

    }

    return `${minutes}:${String(seconds).padStart(2, '0')}`;

}



function findBoundRoleCardForSound(soundCard) {

    if (!soundCard || soundCard.card_type !== '声音') return null;



    const linkedId = Number(soundCard.linked_card_id || 0);

    if (linkedId > 0) {

        const linkedCard = APP_STATE.cards.find(card => card.id === linkedId && card.card_type === '角色');

        if (linkedCard) return linkedCard;

    }



    const name = (soundCard.name || '').trim();

    if (!name) return null;

    return APP_STATE.cards.find(card => card.card_type === '角色' && (card.name || '').trim() === name) || null;

}



// 渲染单个主体卡片

function renderSubjectCard(card) {

    const isSoundCard = card.card_type === '声音';

    const boundRoleCard = isSoundCard ? findBoundRoleCardForSound(card) : null;

    const displayImage = isSoundCard

        ? (boundRoleCard ? getCardPreviewImage(boundRoleCard) : null)

        : getCardPreviewImage(card);

    const referenceAudio = isSoundCard ? getCardReferenceAudio(card) : null;

    const audioCount = isSoundCard && Array.isArray(card.audios) ? card.audios.length : 0;

    const isRoleCard = card.card_type === '角色';

    const isProtagonist = Boolean(card.is_protagonist) && (card.protagonist_gender === 'male' || card.protagonist_gender === 'female');

    const protagonistLabel = card.protagonist_gender === 'female' ? '女主' : '男主';



    // AI提示词生成状态标绛?

    const aiPromptStatus = card.ai_prompt_status || 'idle';

    const hasPrompt = card.ai_prompt && card.ai_prompt.trim().length > 0;

    let statusLabel = '';



    if (isSoundCard) {

        const bindText = boundRoleCard ? `已绑定:${escapeHtml(boundRoleCard.name || '')}` : '未绑定角色';

        statusLabel = audioCount > 0

            ? `<span class="card-ai-prompt-status status-completed">${bindText} / 音频${audioCount}条</span>`

            : `<span class="card-ai-prompt-status">${bindText}</span>`;

    } else if (aiPromptStatus === 'generating') {

        statusLabel = '<span class="card-ai-prompt-status status-generating">提示词生成中</span>';

    } else if (aiPromptStatus === 'failed') {

        statusLabel = '<span class="card-ai-prompt-status status-failed">生成失败</span>';

    } else if (hasPrompt) {

        const cardTypeText = getSubjectPromptStatusText(card.card_type);

        statusLabel = `<span class="card-ai-prompt-status status-completed">${cardTypeText}</span>`;

    }



    const protagonistBadge = (isRoleCard && isProtagonist)

        ? `<span class="card-protagonist-badge ${card.protagonist_gender}">${protagonistLabel}</span>`

        : '';



    const protagonistActions = isRoleCard ? `

        <div class="card-protagonist-actions">

            <button

                class="card-protagonist-btn ${isProtagonist && card.protagonist_gender === 'male' ? 'active' : ''}"

                onclick="event.stopPropagation(); setCardProtagonist(${card.id}, 'male')"

            >设为男主</button>

            <button

                class="card-protagonist-btn ${isProtagonist && card.protagonist_gender === 'female' ? 'active' : ''}"

                onclick="event.stopPropagation(); setCardProtagonist(${card.id}, 'female')"

            >设为女主</button>

        </div>

    ` : '';



    const bodyHtml = isSoundCard ? `

            <div class="card-image-container" onclick="selectCardForPrompt(${card.id})">

                ${displayImage

                    ? `<img class="card-image" src="${getImageUrl(displayImage)}" alt="${escapeHtml(card.name)}">`

                    : `<div class="card-image-placeholder">${referenceAudio ? 'AUDIO READY' : 'NO AUDIO'}</div>`

                }

                <div class="card-upload-overlay">

                    <button class="upload-button" onclick="event.stopPropagation(); uploadSubjectAudio(${card.id})">

                        上传音频

                    </button>

                </div>

                ${referenceAudio ? `

                    <button class="card-expand-button" onclick="event.stopPropagation(); toggleSubjectAudioPreview(${card.id}, this)">试听</button>

                ` : ''}

            </div>

    ` : `

            <div class="card-image-container" onclick="selectCardForPrompt(${card.id})">

                ${displayImage

                    ? `<img class="card-image" src="${getImageUrl(displayImage)}" alt="${escapeHtml(card.name)}">`

                    : '<div class="card-image-placeholder">NO IMAGE</div>'

                }

                <div class="card-upload-overlay">

                    <button class="upload-button" onclick="event.stopPropagation(); uploadSubjectImage(${card.id})">

                        上传图片

                    </button>

                </div>

                ${displayImage ? `

                    <button class="card-expand-button" onclick="event.stopPropagation(); openSubjectImageModal(${card.id})">预览</button>

                ` : ''}

            </div>

    `;



    return `

        <div class="subject-card" data-card-id="${card.id}">

            <div class="card-header">

                <div class="card-name">

                    <span class="card-name-text" onclick="showToast('请到分镜表界面进行修改', 'info')" style="cursor: not-allowed;">${escapeHtml(card.name)}</span>

                    <!-- 鍒О已隐钘?-->

                </div>

                <div class="card-header-right">

                    ${statusLabel}

                    ${protagonistBadge}

                    <button class="card-delete" onclick="deleteSubjectCard(${card.id})">×</button>

                </div>

            </div>

            ${protagonistActions}

            ${bodyHtml}

        </div>

    `;

}



// 选择卡片查看/编辑prompt

async function selectCardForPrompt(cardId) {

    const card = APP_STATE.cards.find(c => c.id === cardId);

    if (!card) return;



    APP_STATE.selectedCardForPrompt = cardId;

    // 清空之前选择的AI作图鍙傝€冨浘

    APP_STATE.selectedReferenceImagesForGeneration = [];

    saveAppState();



    const sidebar = document.getElementById('subjectSidebar');

    if (card.card_type === '声音') {

        renderSoundCardSidebar(card, sidebar);

        return;

    }



    // 加载风格模板列表

    let styleTemplates = [];

    let defaultTemplateId = null;

    try {

        const response = await apiRequest('/api/style-templates');

        styleTemplates = await response.json();



        // 查找榛樿模板

        const defaultTemplate = styleTemplates.find(t => t.is_default);

        if (defaultTemplate) {

            defaultTemplateId = defaultTemplate.id;

        }

    } catch (error) {

        console.error('Failed to load style templates:', error);

    }



    // 获取当前卡片鐨勯格模板ID

    // 如果卡片没有选择模板，则使用榛樿模板

    let currentStyleTemplateId = card.style_template_id || '';

    if (!currentStyleTemplateId && defaultTemplateId) {

        currentStyleTemplateId = defaultTemplateId;



        // 鑷姩保存榛樿模板到数鎹簱

        try {

            await apiRequest(`/api/cards/${cardId}`, {

                method: 'PUT',

                body: JSON.stringify({

                    style_template_id: defaultTemplateId

                })

            });

            // 更新鏈湴鐘舵€?

            card.style_template_id = defaultTemplateId;

            const cardInState = APP_STATE.cards.find(c => c.id === cardId);

            if (cardInState) {

                cardInState.style_template_id = defaultTemplateId;

            }

        } catch (error) {

            console.error('Failed to save default template:', error);

        }

    }



    // 清理ai_prompt：去除格式化前缀，只保留绾补的描杩?

    function cleanPrompt(prompt, cardType) {

        if (!prompt) return '';



        // 去除角色类型的格式化前缀

        if (cardType === '角色') {

            // 匹配"生成图片涓色的外貌鏄細xxxx" 鎴?"生成图片涓色的鏄細xxxx"

            const match = prompt.match(/生成图片中角色的(?:外貌)?是[：:]\s*(.+)/s);

            if (match) return match[1].trim();

        }



        // 去除场景类型的格式化前缀

        if (cardType === '场景') {

            // 匹配"生成图片涓満鏅殑鏄細xxxx"

            const match = prompt.match(/生成图片中场景的是[：:]\s*(.+)/s);

            if (match) return match[1].trim();

        }



        // 如果没有匹配到格式化前缀，直接返回原鏂?

        return prompt;

    }



    // 清理后的prompt

    const cleanedPrompt = cleanPrompt(card.ai_prompt || '', card.card_type);

    const rolePersonalityValue = escapeHtml(card.role_personality || card.role_personality_en || '');

    const isRoleCard = (card.card_type || '').trim() === '角色';



    // 根据卡片类型生成不同的placeholder

    const promptPlaceholder = getSubjectPromptPlaceholder(card.card_type);



    // 妫€鏌I提示词生成状鎬?

    const aiPromptStatus = card.ai_prompt_status || 'idle';

    let promptValue = escapeHtml(cleanedPrompt);

    let promptStyle = '';



    if (aiPromptStatus === 'generating') {

        // 正在生成涓紝显示占位绗?

        promptValue = '生成中，请稍候...';

        promptStyle = 'color: #888;';

    }



    const rolePersonalityField = isRoleCard ? `

            <div class="form-group">

                <label class="form-label">角色性格</label>

                <textarea class="form-textarea" id="cardRolePersonalityInput" rows="3" placeholder="用中文一句话描述角色性格（例如：外冷内热，做事果断，但对亲近的人很温柔）">${rolePersonalityValue}</textarea>

            </div>

    ` : '';



    sidebar.innerHTML = `

        <div class="subject-sidebar-title">${escapeHtml(card.name)} - ${escapeHtml(card.card_type)}</div>

        <div class="subject-sidebar-content">

            <div class="form-group" style="margin-bottom: 15px;">

                <label class="form-label">绘图风格模板</label>

                <select class="form-input" id="cardStyleTemplateSelect">

                    <option value="">-- 选择风格 --</option>

                    ${styleTemplates.map(t => `

                        <option value="${t.id}" ${t.id === currentStyleTemplateId ? 'selected' : ''}>

                            ${escapeHtml(t.name)}

                        </option>

                    `).join('')}

                </select>

            </div>

            <div class="form-group">

                <label class="form-label">${card.card_type}描述</label>

                <textarea class="form-textarea" id="cardPromptInput" rows="10" placeholder="${promptPlaceholder}" style="${promptStyle}">${promptValue}</textarea>

            </div>

            ${rolePersonalityField}

            <div style="margin-top: 10px; display: flex; gap: 8px;">

                <button class="secondary-button" onclick="generateCardAiPrompt()">生成AI提示词</button>

            </div>

        </div>



        <div style="border-top: 1px solid #2a2a2a; margin: 20px 0; padding-top: 20px;">

            <div class="subject-sidebar-title">AI作图</div>

            <div style="display: flex; gap: 8px; margin-bottom: 10px;">

                <select class="form-input" id="imageProviderSelect" onchange="updateImageProviderModels()" style="flex: 1;">

                    <option value="">选择服务商</option>

                </select>

                <select class="form-input" id="imageModelSelect" onchange="updateImageGenerationParams()" style="flex: 1;">

                    <option value="">选择模型</option>

                </select>

                <button class="secondary-button" onclick="generateCardImage()">作图</button>

            </div>

            ${isRoleCard ? `

            <div style="display: flex; gap: 8px; margin-bottom: 10px;">

                <button class="secondary-button" onclick="generateCardImage('three_view')" style="width: 100%;">生成三视图</button>

            </div>

            ` : ''}

            <div id="imageGenerationParams" style="display: none; margin-bottom: 15px; padding: 12px; background: #0a0a0a; border: 1px solid #2a2a2a; border-radius: 4px;">

                <!-- 参数面板鍔ㄦ€佺敓鎴?-->

            </div>

        </div>



        <div style="border-top: 1px solid #2a2a2a; margin: 20px 0; padding-top: 20px;">

            <div class="subject-sidebar-title">主体素材图</div>

            <div id="generatedImagesContainer" style="margin-top: 10px;">

                <div class="loading" style="padding: 20px; font-size: 12px;">加载中...</div>

            </div>

        </div>

    `;



    // 加载模型配置

    await loadImageModels();



    // 加载生成的图片，如果鏈夊理中的图片，鍚姩杞

    const hasProcessing = await loadGeneratedImages(cardId);

    if (hasProcessing) {

        startImageStatusPolling();

    } else {

        // 没有处理涓殑图片，确保停止轮璇?

        stopImageStatusPolling();

    }



    const promptInputEl = document.getElementById('cardPromptInput');

    if (promptInputEl) {

        promptInputEl.addEventListener('blur', () => {

            saveCardPrompt({ silent: true });

        });

    }



    const rolePersonalityInputEl = document.getElementById('cardRolePersonalityInput');

    if (rolePersonalityInputEl) {

        rolePersonalityInputEl.addEventListener('blur', () => {

            saveCardPrompt({ silent: true });

        });

    }



    const styleTemplateSelectEl = document.getElementById('cardStyleTemplateSelect');

    if (styleTemplateSelectEl) {

        styleTemplateSelectEl.addEventListener('change', () => {

            saveCardPrompt({ silent: true });

        });

    }



    // 如果AI提示璇嶆在生成中，启动轮璇?

    if (card.ai_prompt_status === 'generating') {

        pollCardAiPromptStatus(cardId);

    }

}



function renderSoundCardSidebar(card, sidebarEl = null) {

    const sidebar = sidebarEl || document.getElementById('subjectSidebar');

    if (!sidebar) return;



    const audios = Array.isArray(card.audios) ? card.audios : [];

    const referenceAudio = getCardReferenceAudio(card);

    const roleCards = APP_STATE.cards.filter(item => item.card_type === '角色');

    const boundRoleCard = findBoundRoleCardForSound(card);

    const selectedLinkedId = Number(card.linked_card_id || (boundRoleCard ? boundRoleCard.id : 0) || 0);

    const listHtml = audios.length > 0

        ? audios.map((audio, index) => {

            const isReference = Boolean(audio?.is_reference);

            const name = (audio?.file_name || '').trim() || `音频${index + 1}`;

            const createdAt = (audio?.created_at || '').replace('T', ' ').split('.')[0];

            const durationLabel = formatAudioDurationLabel(audio?.duration_seconds);

            return `

                <div style="padding: 8px 10px; border: 1px solid #2a2a2a; border-radius: 6px; background: #0a0a0a; display: flex; justify-content: space-between; gap: 8px;">

                    <div style="min-width: 0;">

                        <div style="font-size: 12px; color: #e2e8f0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(name)}</div>

                        <div style="font-size: 11px; color: #8b949e;">${escapeHtml(createdAt || '')}</div>

                        <div style="font-size: 11px; color: #8b949e;">时长：${escapeHtml(durationLabel)}</div>

                    </div>

                    <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 6px; flex-shrink: 0;">

                        ${isReference ? '<span style="font-size: 11px; color: #4ade80;">当前素材</span>' : '<span style="font-size: 11px; color: #8b949e;">历史素材</span>'}

                        <button class="secondary-button" style="padding: 4px 8px; font-size: 11px;" onclick="deleteSubjectAudio(${card.id}, ${audio.id})">删除</button>

                    </div>

                </div>

            `;

        }).join('')

        : '<div class="subject-sidebar-empty">暂无声音素材</div>';



    sidebar.innerHTML = `

        <div class="subject-sidebar-title">${escapeHtml(card.name)} - 声音</div>

        <div class="subject-sidebar-content">

            <div class="form-group" style="margin-bottom: 12px;">

                <label class="form-label">绑定角色卡片</label>

                <div style="display: flex; gap: 8px;">

                    <select class="form-input" id="soundCardLinkedRoleSelect" style="flex: 1;">

                        <option value="">不绑定</option>

                        ${roleCards.map(role => `

                            <option value="${role.id}" ${selectedLinkedId === role.id ? 'selected' : ''}>${escapeHtml(role.name || '')}</option>

                        `).join('')}

                    </select>

                    <button class="secondary-button" onclick="saveSoundCardBinding(${card.id})">保存绑定</button>

                </div>

            </div>

            <div style="display: flex; gap: 8px; margin-bottom: 12px;">

                <button class="secondary-button" onclick="uploadSubjectAudio(${card.id})">上传音频</button>

                <button class="primary-button" onclick="toggleSubjectAudioPreview(${card.id}, this)" ${referenceAudio ? '' : 'disabled'}>

                    ${referenceAudio ? '试听当前素材' : '暂无可试听音频'}

                </button>

            </div>

            <div style="display: flex; flex-direction: column; gap: 8px; max-height: 280px; overflow-y: auto;">

                ${listHtml}

            </div>

        </div>

    `;

}



async function saveSoundCardBinding(cardId) {

    const selectEl = document.getElementById('soundCardLinkedRoleSelect');

    if (!selectEl) return;



    const rawValue = (selectEl.value || '').trim();

    const linkedCardId = rawValue ? Number(rawValue) : null;

    if (rawValue && (!Number.isFinite(linkedCardId) || linkedCardId <= 0)) {

        showToast('绑定角色选择无效', 'error');

        return;

    }



    try {

        const response = await apiRequest(`/api/cards/${cardId}`, {

            method: 'PUT',

            body: JSON.stringify({

                linked_card_id: linkedCardId

            })

        });



        if (response && response.ok) {

            showToast('绑定已保存', 'success');

            await reloadSubjectStepPreserveState();

        } else {

            const error = await response.json().catch(() => ({}));

            showToast(error.detail || '保存绑定失败', 'error');

        }

    } catch (error) {

        console.error('Failed to save sound card binding:', error);

        showToast('保存绑定失败', 'error');

    }

}



// 保存卡片prompt

async function saveCardPrompt(options = {}) {

    const silent = Boolean(options.silent);

    const promptInput = document.getElementById('cardPromptInput');

    if (!promptInput || !APP_STATE.selectedCardForPrompt) {

        return false;

    }



    const prompt = promptInput.value;

    if (prompt === '生成中，请稍候...') {

        return false;

    }



    const styleTemplateSelect = document.getElementById('cardStyleTemplateSelect');

    const styleTemplateId = styleTemplateSelect ? (styleTemplateSelect.value || null) : null;

    const rolePersonalityInput = document.getElementById('cardRolePersonalityInput');

    const rolePersonality = rolePersonalityInput ? rolePersonalityInput.value : '';



    try {

        const body = { ai_prompt: prompt };

        if (styleTemplateId !== null) {

            body.style_template_id = parseInt(styleTemplateId) || null;

        }

        if (rolePersonalityInput) {

            body.role_personality = rolePersonality;

        }



        const response = await apiRequest(`/api/cards/${APP_STATE.selectedCardForPrompt}`, {

            method: 'PUT',

            body: JSON.stringify(body)

        });



        if (response.ok) {

            // 更新鏈湴数据

            const card = APP_STATE.cards.find(c => c.id === APP_STATE.selectedCardForPrompt);

            if (card) {

                card.ai_prompt = prompt;

                card.style_template_id = body.style_template_id;

                if (rolePersonalityInput) {

                    card.role_personality = rolePersonality;

                }

            }



            if (!silent) {

                showToast('保存成功', 'success');

            }

            return true;

        } else {

            if (!silent) {

                showToast('保存失败', 'error');

            }

            return false;

        }

    } catch (error) {

        console.error('Failed to save prompt:', error);

        if (!silent) {

            showToast('保存失败', 'error');

        }

        return false;

    }

}



// 生成单个主体的AI提示璇?

async function generateCardAiPrompt() {

    if (!APP_STATE.selectedCardForPrompt) return;



    const button = event.target;

    const originalText = button.textContent;



    try {

        button.textContent = '生成中...';

        button.disabled = true;



        const response = await apiRequest(`/api/cards/${APP_STATE.selectedCardForPrompt}/generate-ai-prompt`, {

            method: 'POST'

        });



        if (response.ok) {

            const result = await response.json();



            // 立即更新鏈湴卡片鐘舵€佷负 'generating'

            const localCard = APP_STATE.cards.find(c => c.id === APP_STATE.selectedCardForPrompt);

            if (localCard) {

                localCard.ai_prompt_status = 'generating';

            }



            // 立即更新侧边栏输鍏ユ显示生成涓?

            const promptInput = document.getElementById('cardPromptInput');

            if (promptInput) {

                promptInput.value = '生成中，请稍候...';

                promptInput.style.color = '#888';

            }



            // 重新渲染卡片列表，显示生成中鐘舵€?

            renderSubjectCards();



            showToast('已开始生成AI提示词', 'info');



            // 寮€始轮询状鎬?

            pollCardAiPromptStatus(APP_STATE.selectedCardForPrompt);

        } else {

            showToast('生成失败', 'error');

        }

    } catch (error) {

        console.error('生成AI提示词失璐?', error);

        showToast('生成失败', 'error');

    } finally {

        button.textContent = originalText;

        button.disabled = false;

    }

}



// 杞主体AI提示词生成状鎬?

async function pollCardAiPromptStatus(cardId) {

    const maxAttempts = 60; // 鏈€多轮璇?0次（5分钟锛?

    let attempts = 0;



    const poll = async () => {

        if (attempts >= maxAttempts) {

            showToast('生成超时，请刷新页面查看结果', 'warning');

            return;

        }



        attempts++;



        try {

            const response = await apiRequest(`/api/cards/${cardId}`);

            if (response.ok) {

                const card = await response.json();



                if (card.ai_prompt_status === 'completed') {

                    // 重新获取鎵€有卡片数鎹紝纭繚鐘舵€佸悓姝?

                    await refreshSubjectCardsData();



                    // 如果当前正在查看这个卡片，更新显绀?

                    if (APP_STATE.selectedCardForPrompt === cardId) {

                        const input = document.getElementById('cardPromptInput');

                        if (input) {

                            // 清理提示词（去除格式化前缂€锛?

                            const cleanedPrompt = card.ai_prompt || '';

                            const match = cleanedPrompt.match(/生成图片中(?:角色|场景)的(?:外貌)?是[：:]\s*(.+)/s);

                            input.value = match ? match[1].trim() : cleanedPrompt;

                            // 鎭㈠正常文字颜色

                            input.style.color = '';

                        }

                    }



                    // 重新渲染卡片列表，显示最新状鎬?

                    renderSubjectCards();



                    showToast('AI提示词生成成功', 'success');

                    return;

                } else if (card.ai_prompt_status === 'failed') {

                    // 重新获取鎵€有卡片数鎹紝纭繚鐘舵€佸悓姝?

                    await refreshSubjectCardsData();



                    // 如果当前正在查看这个卡片，清除占浣嶇文字，恢复原鍐呭

                    if (APP_STATE.selectedCardForPrompt === cardId) {

                        const input = document.getElementById('cardPromptInput');

                        if (input && input.value === '生成中，请稍候...') {

                            // 鎭㈠到原来的提示词内容（鍙兘鏄┖的）

                            input.value = card.ai_prompt || '';

                            input.style.color = '';

                        }

                    }



                    // 重新渲染卡片列表，显示失败状鎬?

                    renderSubjectCards();



                    showToast('AI提示词生成失败', 'error');

                    return;

                } else if (card.ai_prompt_status === 'generating') {

                    // 继续杞

                    setTimeout(poll, AI_PROMPT_STATUS_POLL_INTERVAL_MS);

                }

            }

        } catch (error) {

            console.error('杞鐘舵€佸け璐?', error);

        }

    };



    poll();

}



// 批量生成绘画提示璇?

async function batchGenerateAiPrompts() {

    if (!APP_STATE.library || !APP_STATE.library.id) {

        showToast('未找到主体库', 'error');

        return;

    }



    const confirmed = await showConfirmModal(

        '将为所有没有 AI 提示词的主体生成提示词，这可能需要一些时间。是否继续？',

        '批量生成绘画提示词'

    );



    if (!confirmed) return;



    try {

        showToast('正在批量生成绘画提示词...', 'info');



        const response = await apiRequest(`/api/libraries/${APP_STATE.library.id}/batch-generate-prompts`, {

            method: 'POST'

        });



        if (response.ok) {

            const result = await response.json();

            showToast(result.message, result.failed_count > 0 ? 'warning' : 'success');



            // 刷新主体数据

            await refreshSubjectCardsData();

            renderSubjectCards();



            // 如果当前鏈夐€変腑的卡片，重新渲染侧边鏍?

            if (APP_STATE.selectedCardForPrompt) {

                const card = APP_STATE.cards.find(c => c.id === APP_STATE.selectedCardForPrompt);

                if (card) {

                    await selectCardForPrompt(card.id);

                }

            }

        } else {

            const error = await response.json();

            showToast(error.detail || '批量生成失败', 'error');

        }

    } catch (error) {

        console.error('Failed to batch generate AI prompts:', error);

        showToast('批量生成失败', 'error');

    }

}



async function refreshSubjectCardsData() {

    // 从episode获取library_id

    const episodesResponse = await apiRequest(`/api/scripts/${APP_STATE.currentScript}/episodes`);

    const episodes = await episodesResponse.json();

    const episode = episodes.find(ep => ep.id === APP_STATE.currentEpisode);



    if (!episode) {

        throw new Error('该剧集不存在');

    }



    if (!episode.library_id) {

        throw new Error('该剧集没有关联的主体库');

    }



    APP_STATE.library = { id: episode.library_id };



    APP_STATE.cards = await ensureSubjectCardsWithDefaultSound(episode.library_id);

}



async function reloadSubjectStepPreserveState() {

    const container = document.getElementById('creationContainer');

    const cardsArea = container ? container.querySelector('.subject-cards-area') : null;

    const scrollTop = cardsArea ? cardsArea.scrollTop : 0;

    const selectedCardId = APP_STATE.selectedCardForPrompt;



    // 鉁?保存右侧生成图片列表的滚动位缃?

    const generatedImagesContainer = document.getElementById('generatedImagesContainer');

    const imagesScrollTop = generatedImagesContainer ? generatedImagesContainer.scrollTop : 0;



    const isSubjectView = container && container.querySelector('.subject-layout');

    if (APP_STATE.currentStep === 1 && isSubjectView) {

        try {

            await refreshSubjectCardsData();

            renderSubjectCards();

        } catch (error) {

            console.error('Failed to refresh subject cards:', error);

            await loadSubjectStep();

        }

    } else {

        await loadSubjectStep();

    }



    const refreshedContainer = document.getElementById('creationContainer');

    const refreshedArea = refreshedContainer ? refreshedContainer.querySelector('.subject-cards-area') : null;

    if (refreshedArea) {

        refreshedArea.scrollTop = scrollTop;

    }



    if (selectedCardId) {

        const exists = APP_STATE.cards.some(card => card.id === selectedCardId);

        if (exists) {

            await selectCardForPrompt(selectedCardId);



            // 鉁?鎭㈠右侧生成图片列表的滚动位缃?

            setTimeout(() => {

                const refreshedImagesContainer = document.getElementById('generatedImagesContainer');

                if (refreshedImagesContainer) {

                    refreshedImagesContainer.scrollTop = imagesScrollTop;

                }

            }, 100);

        } else {

            APP_STATE.selectedCardForPrompt = null;

        }

    }

}



// 创建指定类型的主体卡鐗?

async function createSubjectCardByType(cardType) {

    const resolvedType = resolveSubjectCardType(cardType);



    try {

        const response = await apiRequest(`/api/libraries/${APP_STATE.library.id}/cards`, {

            method: 'POST',

            body: JSON.stringify({

                name: getDefaultSubjectCardName(resolvedType),

                card_type: resolvedType

            })

        });



        if (response.ok) {

            await reloadSubjectStepPreserveState();

        } else {

            alert('创建失败');

        }

    } catch (error) {

        console.error('Failed to create card:', error);

        alert('创建失败');

    }

}



// 创建主体卡片（保留旧接口锛?

async function createSubjectCard() {

    await createSubjectCardByType('角色');

}



// 切换类型下拉妗?

function toggleTypeDropdown(cardId) {

    const dropdown = document.getElementById(`typeDropdown-${cardId}`);

    const allDropdowns = document.querySelectorAll('.type-dropdown');



    allDropdowns.forEach(d => {

        if (d.id !== `typeDropdown-${cardId}`) {

            d.classList.remove('active');

        }

    });



    dropdown.classList.toggle('active');



    setTimeout(() => {

        document.addEventListener('click', function closeDropdown(e) {

            if (!e.target.closest('.card-type-selector')) {

                dropdown.classList.remove('active');

                document.removeEventListener('click', closeDropdown);

            }

        });

    }, 0);

}



// 淇敼卡片类型

async function changeCardType(cardId, newType) {

    try {

        const response = await apiRequest(`/api/cards/${cardId}`, {

            method: 'PUT',

            body: JSON.stringify({ card_type: newType })

        });



        if (response.ok) {

            await reloadSubjectStepPreserveState();

        }

    } catch (error) {

        console.error('Failed to change card type:', error);

    }

}



async function setCardProtagonist(cardId, gender) {

    const normalizedGender = (gender || '').trim().toLowerCase();

    const card = APP_STATE.cards.find(c => c.id === cardId);

    const currentIsProtagonist = Boolean(card?.is_protagonist) &&

        (card?.protagonist_gender === 'male' || card?.protagonist_gender === 'female');

    const currentGender = (card?.protagonist_gender || '').trim().toLowerCase();

    const sameGenderClicked = (normalizedGender === 'male' || normalizedGender === 'female') &&

        currentIsProtagonist &&

        currentGender === normalizedGender;



    const body = {};

    if (sameGenderClicked) {

        body.is_protagonist = false;

        body.protagonist_gender = '';

    } else if (normalizedGender === 'male' || normalizedGender === 'female') {

        body.is_protagonist = true;

        body.protagonist_gender = normalizedGender;

    } else {

        body.is_protagonist = false;

        body.protagonist_gender = '';

    }



    try {

        const response = await apiRequest(`/api/cards/${cardId}`, {

            method: 'PUT',

            body: JSON.stringify(body)

        });



        if (response.ok) {

            await reloadSubjectStepPreserveState();

            if (sameGenderClicked) {

                showToast('已取消主角设置', 'success');

            } else if (normalizedGender === 'male') {

                showToast('已设置为男主', 'success');

            } else if (normalizedGender === 'female') {

                showToast('已设置为女主', 'success');

            } else {

                showToast('已取消主角设置', 'success');

            }

        } else {

            const error = await response.json().catch(() => ({}));

            showToast(error.detail || '设置失败', 'error');

        }

    } catch (error) {

        console.error('Failed to set protagonist:', error);

        showToast('设置失败', 'error');

    }

}



// 编辑卡片名称

async function editCardName(cardId) {

    const card = APP_STATE.cards.find(c => c.id === cardId);

    const newName = await showInputModal('修改名称', '请输入新名称', card.name);

    if (!newName || newName === card.name) return;



    try {

        const response = await apiRequest(`/api/cards/${cardId}`, {

            method: 'PUT',

            body: JSON.stringify({ name: newName.trim() })

        });



        if (response.ok) {

            await reloadSubjectStepPreserveState();

        } else {

            alert('修改失败');

        }

    } catch (error) {

        console.error('Failed to edit card name:', error);

        alert('修改失败');

    }

}



// 编辑卡片鍒О

async function editCardAlias(cardId) {

    const card = APP_STATE.cards.find(c => c.id === cardId);

    if (!card) return;



    const newAlias = await showInputModal('修改别称', '请输入别称', card.alias || '');

    if (newAlias === null) return;



    const aliasValue = newAlias.trim();

    if (aliasValue === (card.alias || '')) return;



    try {

        const response = await apiRequest(`/api/cards/${cardId}`, {

            method: 'PUT',

            body: JSON.stringify({ alias: aliasValue })

        });



        if (response.ok) {

            await reloadSubjectStepPreserveState();

        } else {

            alert('修改失败');

        }

    } catch (error) {

        console.error('Failed to edit alias:', error);

        alert('修改失败');

    }

}



// 删除主体卡片

async function deleteSubjectCard(cardId) {

    const confirmed = await showConfirmModal('确定要删除这个主体吗？');

    if (!confirmed) return;



    try {

        if (subjectAudioPreviewCardId === cardId) {

            stopSubjectAudioPreview();

        }

        const response = await apiRequest(`/api/cards/${cardId}`, {

            method: 'DELETE'

        });



        if (response.ok) {

            await reloadSubjectStepPreserveState();

        } else {

            alert('删除失败');

        }

    } catch (error) {

        console.error('Failed to delete card:', error);

        alert('删除失败');

    }

}



// 上传主体图片

async function uploadSubjectImage(cardId) {

    const input = document.createElement('input');

    input.type = 'file';

    input.accept = 'image/*';

    input.multiple = true;



    input.onchange = async (e) => {

        const files = e.target.files;

        if (!files || files.length === 0) return;



        for (const file of files) {

            const formData = new FormData();

            formData.append('file', file);



            try {

                const response = await apiRequest(`/api/cards/${cardId}/images`, {

                    method: 'POST',

                    body: formData

                });



                if (!response.ok) {

                    alert(`上传 ${file.name} 失败`);

                }

            } catch (error) {

                console.error('Failed to upload image:', error);

                alert(`上传 ${file.name} 失败`);

            }

        }



        await reloadSubjectStepPreserveState();

    };



    input.click();

}



function stopSubjectAudioPreview() {

    if (subjectAudioPreviewPlayer) {

        try {

            subjectAudioPreviewPlayer.pause();

            subjectAudioPreviewPlayer.currentTime = 0;

        } catch (error) {

            console.warn('Failed to stop subject audio preview:', error);

        }

    }



    if (subjectAudioPreviewButton) {

        const restoreLabel = subjectAudioPreviewButton.dataset.previewLabel || '试听';

        subjectAudioPreviewButton.textContent = restoreLabel;

        subjectAudioPreviewButton.classList.remove('playing');

    }



    subjectAudioPreviewPlayer = null;

    subjectAudioPreviewButton = null;

    subjectAudioPreviewCardId = null;

}



async function toggleSubjectAudioPreview(cardId, buttonEl = null) {

    const card = APP_STATE.cards.find(c => c.id === cardId);

    const referenceAudio = getCardReferenceAudio(card);

    const audioUrl = referenceAudio ? getImageUrl(referenceAudio.audio_path || '') : '';



    if (!audioUrl) {

        showToast('该声音卡片暂无可试听音频', 'warning');

        return;

    }



    if (subjectAudioPreviewPlayer && subjectAudioPreviewCardId === cardId) {

        stopSubjectAudioPreview();

        return;

    }



    stopSubjectAudioPreview();



    const audio = new Audio(audioUrl);

    subjectAudioPreviewPlayer = audio;

    subjectAudioPreviewCardId = cardId;

    subjectAudioPreviewButton = buttonEl || null;



    if (buttonEl) {

        buttonEl.dataset.previewLabel = buttonEl.textContent || '试听';

        buttonEl.textContent = '停止';

        buttonEl.classList.add('playing');

    }



    audio.onended = () => {

        stopSubjectAudioPreview();

    };

    audio.onerror = () => {

        showToast('试听失败', 'error');

        stopSubjectAudioPreview();

    };



    try {

        await audio.play();

    } catch (error) {

        showToast('试听失败: ' + error.message, 'error');

        stopSubjectAudioPreview();

    }

}



async function uploadSubjectAudio(cardId) {

    const input = document.createElement('input');

    input.type = 'file';

    input.accept = 'audio/*';

    input.multiple = true;



    input.onchange = async (e) => {

        const files = e.target.files;

        if (!files || files.length === 0) return;



        for (const file of files) {

            const formData = new FormData();

            formData.append('file', file);



            try {

                const response = await apiRequest(`/api/cards/${cardId}/audios`, {

                    method: 'POST',

                    body: formData

                });



                if (!response || !response.ok) {

                    showToast(`上传 ${file.name} 失败`, 'error');

                }

            } catch (error) {

                console.error('Failed to upload audio:', error);

                showToast(`上传 ${file.name} 失败`, 'error');

            }

        }



        await reloadSubjectStepPreserveState();

    };



    input.click();

}



async function deleteSubjectAudio(cardId, audioId) {

    const confirmed = await showConfirmModal('确定要删除这条音频吗？');

    if (!confirmed) return;



    try {

        if (subjectAudioPreviewCardId === cardId) {

            stopSubjectAudioPreview();

        }



        const response = await apiRequest(`/api/cards/${cardId}/audios/${audioId}`, {

            method: 'DELETE'

        });



        if (response && response.ok) {

            await reloadSubjectStepPreserveState();

            showToast('音频已删除', 'success');

        } else {

            const error = await response.json().catch(() => ({}));

            showToast(error.detail || '删除音频失败', 'error');

        }

    } catch (error) {

        console.error('Failed to delete audio:', error);

        showToast('删除音频失败', 'error');

    }

}



// 打开主体图片妯℃€佹

async function openSubjectImageModal(cardId) {

    const card = APP_STATE.cards.find(c => c.id === cardId);

    if (!card) return;



    // 收集鍙敤图片：优先使用勾选的鍙傝€冨浘，没有则使用上传图片

    const allImages = [];



    const referenceGeneratedImages = (card.generated_images || [])

        .filter(img => img.status === 'completed' && img.is_reference)

        .map(img => ({ id: img.id, image_path: img.image_path }));



    const usingReferenceImages = referenceGeneratedImages.length > 0;



    if (usingReferenceImages) {

        allImages.push(...referenceGeneratedImages);

    } else if (card.images && card.images.length > 0) {

        allImages.push(...card.images);

    }



    // 如果没有任何图片，直接返鍥?

    if (allImages.length === 0) {

        showToast('暂无参考图', 'info');

        return;

    }



    APP_STATE.imageModal = {

        isOpen: true,

        images: allImages,

        currentIndex: 0,

        cardId: cardId

    };



    updateImageModal();

    document.getElementById('imageModal').classList.add('active');

    document.getElementById('deleteImage').style.display = usingReferenceImages ? 'none' : 'block';

}



// 绗?步：故事板界闈?

async function loadStoryboardStep() {

    const container = document.getElementById('creationContainer');

    if (!container) {

        return;

    }

    setContentTightBottom(true);



    container.innerHTML = '<div class="loading">加载中...</div>';



    try {

        stopVideoStatusPolling();

        await loadStoryboardCards();



        // 并发加载鎵€有独立的数据锛堟€ц兘优化锛?

        const [

            episodeResponse,

            managedResponse,

            templatesResponse,

            shotsResponse

        ] = await Promise.all([

            // 加载episode信息

            apiRequest(`/api/episodes/${APP_STATE.currentEpisode}`),

            // 加载鎵樼会话鐘舵€?

            apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/managed-session-status`).catch(err => {

                console.error('[loadStoryboardStep] 获取鎵樼会话鐘舵€佸け璐?', err);

                return null;

            }),

            // 加载模板

            apiRequest('/api/templates'),

            // 加载镜头

            apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/shots`)

        ]);



        // 解析鎵€有响搴?

        APP_STATE.currentEpisodeInfo = await episodeResponse.json();



        if (managedResponse) {

            try {

                APP_STATE.managedSession = await managedResponse.json();

            } catch (err) {

                console.error('[loadStoryboardStep] 解析鎵樼会话鐘舵€佸け璐?', err);

                APP_STATE.managedSession = { status: 'none', total_shots: 0, completed_shots: 0, session_id: null, created_at: null };

            }

        } else {

            APP_STATE.managedSession = { status: 'none', total_shots: 0, completed_shots: 0, session_id: null, created_at: null };

        }



        APP_STATE.templates = await templatesResponse.json();

        APP_STATE.shots = await shotsResponse.json();
        reconcileLocalStoryboardPromptBatchFlag();



        // 调试锛氭鏌hot数据涓槸否有detail_images_status

        if (APP_STATE.shots.length > 0) {

            const firstShot = APP_STATE.shots[0];

            console.log('[loadStoryboardStep] 绗竴涓暅头数鎹?', JSON.stringify(firstShot, null, 2));

            console.log('[loadStoryboardStep] 绗竴涓暅头是否有detail_images_status:', 'detail_images_status' in firstShot, firstShot.detail_images_status);

        }



        // 刷新鎵€鏈夎频的鏈€新状态和URL

        try {

            const refreshResponse = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/refresh-videos`, {

                method: 'POST'

            });

            const refreshResult = await refreshResponse.json();

            console.log('[loadStoryboardStep] 瑙嗛刷新完成:', refreshResult);



            // 如果有更新，重新加载镜头数据

            if (refreshResult.updated_count > 0) {

                const shotsUrl = `/api/episodes/${APP_STATE.currentEpisode}/shots`;

                const reloadResponse = await apiRequest(shotsUrl);

                APP_STATE.shots = await reloadResponse.json();

                console.log(`[loadStoryboardStep] 已更鏂?${refreshResult.updated_count} 涓频URL`);

            }

        } catch (err) {

            console.error('[loadStoryboardStep] 瑙嗛刷新失败:', err);

            // 刷新失败不影响页面加杞?

        }



        // 如果没有镜头，创寤?涓粯认镜澶?

        if (APP_STATE.shots.length === 0) {

            const episodeVideoSettings = getEpisodeStoryboardVideoSettings();

            for (let i = 1; i <= 5; i++) {

                await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/shots`, {

                    method: 'POST',

                    body: JSON.stringify({

                        shot_number: i,

                        prompt_template: '',

                        selected_card_ids: [],

                        aspect_ratio: episodeVideoSettings.aspect_ratio,

                        duration: episodeVideoSettings.duration

                    })

                });

            }



            const shotsUrl = `/api/episodes/${APP_STATE.currentEpisode}/shots`;

            const refreshResponse = await apiRequest(shotsUrl);

            APP_STATE.shots = await refreshResponse.json();

        }



        pruneImportBatchesForEpisode(APP_STATE.currentEpisode, APP_STATE.shots);

        syncEpisodeStoryboardVideoSettingsToShotState();


        APP_STATE.currentShot = APP_STATE.shots[0];

        APP_STATE.currentShotVideos = null;



        const isGenerating = isStoryboardPromptBatchGenerating();
        const isGeneratingReasoning = isStoryboardReasoningPromptBatchGenerating();

        const generateBtnDisabled = isGenerating ? 'disabled' : '';
        const generateReasoningBtnDisabled = isGeneratingReasoning ? 'disabled' : '';

        const statusMessages = [];
        if (isGenerating) {
            statusMessages.push('正在批量生成Sora提示词...');
        }
        if (isGeneratingReasoning) {
            statusMessages.push('正在批量生成推理提示词...');
        }
        const generateStatusHtml = statusMessages.length > 0

            ? `<span class="batch-generate-status">${escapeHtml(statusMessages.join(' '))}</span>`

            : '';



        // 鎵樼鐘舵€侊紙从全灞€鐘舵€佽幏取，后续浼氶€氳繃杞更新锛?

        const managedSession = APP_STATE.managedSession || { status: 'none', total_shots: 0, completed_shots: 0 };

        const managedButtonHtml = getManagedToolbarHtml(managedSession);



        // 根据模式显示不同的工具栏按钮

        let toolbarButtons = `

            ${generateStatusHtml}

            ${managedButtonHtml}

            <button class="secondary-button storyboard-tool-button" data-storyboard-video-setting-btn="1" onclick="openStoryboardVideoSettingModal()">图/视频设置</button>

            <button class="secondary-button storyboard-tool-button" onclick="batchGenerateStoryboardReasoningPrompts()" ${generateReasoningBtnDisabled} id="batchGenerateReasoningBtn">批量生成推理提示词</button>

            <button class="secondary-button storyboard-tool-button" onclick="batchGenerateSoraPrompts()" ${generateBtnDisabled} id="batchGenerateBtn">批量生成Sora提示词</button>

            <button class="secondary-button storyboard-tool-button" onclick="batchGenerateSoraVideos()">批量生成Sora视频</button>

            <button class="secondary-button storyboard-tool-button" onclick="batchDownloadVideos()">批量下载视频</button>

        `;



        container.innerHTML = `

            <div class="storyboard-layout">

                <div class="storyboard-tools">

                    <div class="storyboard-tools-left"></div>

                    <div class="storyboard-tools-right">

                        ${toolbarButtons}

                        <button class="secondary-button storyboard-tool-button" onclick="addNewShot()">新增镜头</button>

                        <div
                            class="storyboard-more-menu"
                            id="storyboardMoreMenu"
                            onmouseenter="cancelHideStoryboardMoreButtons()"
                            onmouseleave="scheduleHideStoryboardMoreButtons()"
                        >

                            <button class="secondary-button storyboard-tool-button" id="storyboardMoreBtn" onclick="toggleStoryboardMoreButtons(event)">更多</button>

                            <div class="storyboard-more-dropdown" id="storyboardMoreBtns">

                                <button class="secondary-button storyboard-tool-button" onclick="editPromptTemplate()">画风模板设置</button>

                                <button class="secondary-button storyboard-tool-button" onclick="editSoraPromptStyle()">Sora提示词设置</button>

                                <button class="secondary-button storyboard-tool-button" onclick="batchGenerateStoryboardImageShots()">批量生成镜头图</button>

                                <button class="secondary-button storyboard-tool-button" onclick="openPromptManagement()">提示词管理界面</button>

                                <button class="secondary-button storyboard-tool-button" onclick="openAdminPanel()">后台管理</button>

                                <button class="secondary-button storyboard-tool-button" onclick="openModelSelectPanel()">模型选择</button>

                                <button class="secondary-button storyboard-tool-button" onclick="openBillingPanel()">计费明细</button>

                                <button class="secondary-button storyboard-tool-button" onclick="openJimengDashboard()">即梦任务查询</button>

                            </div>

                        </div>

                    </div>

                </div>

                <div class="storyboard-main">

                    <div class="storyboard-shots-area" id="storyboardShotsGrid"></div>

                    <div class="storyboard-sidebar" id="storyboardSidebar">

                        <!-- 鍔ㄦ€佸姞杞?-->

                    </div>

                </div>

            </div>

        `;



        refreshShotImageSizeButtonLabels();

        refreshStoryboardVideoSettingButtonLabels();

        renderStoryboardShotsGrid();

        renderStoryboardSidebar();



        // 妫€查是否有处理涓殑任务，根鎹ā寮忔查不同的鐘舵€佸瓧娈?

        console.log('[loadStoryboardStep] 妫€查是否有处理涓换务，shots数量:', APP_STATE.shots.length);



        const processingShots = APP_STATE.shots.filter(s =>

            s.video_status === 'processing' ||

            s.video_status === 'submitting' ||

            s.video_status === 'preparing' ||

            s.sora_prompt_status === 'generating' ||

            s.reasoning_prompt_status === 'generating' ||

            s.storyboard_image_status === 'processing'

        );



        console.log('[loadStoryboardStep] 处理涓殑镜头:', processingShots.map(s => ({

            id: s.id,

            shot_number: s.shot_number,

            video_status: s.video_status,

            sora_status: s.sora_prompt_status,

            reasoning_status: s.reasoning_prompt_status,

            storyboard_image_status: s.storyboard_image_status

        })));



        const hasProcessingTasks = processingShots.length > 0;

        console.log('[loadStoryboardStep] 闇€要启动轮璇?', hasProcessingTasks);



        if (hasProcessingTasks) {

            startVideoStatusPolling();

        }



        // 如果有仍在收尾的托管会话，启动轮询

        if (APP_STATE.managedSession && isManagedSessionActiveStatus(APP_STATE.managedSession.status)) {

            console.log('[loadStoryboardStep] 检测到活跃托管会话，启动托管轮询');

            startManagedSessionPolling();

        }



    } catch (error) {

        console.error('Failed to load storyboard:', error);

        container.innerHTML = '<div class="empty-state">加载失败</div>';

    }

}



async function loadStoryboard2Step() {

    const container = document.getElementById('creationContainer');

    if (!container) {

        return;

    }

    setContentTightBottom(true);

    container.innerHTML = '<div class="loading">加载中...</div>';



    try {

        stopVideoStatusPolling();

        stopManagedSessionPolling();

        stopStoryboard2GenerationPolling();

        APP_STATE.storyboard2GeneratingBySubShot = {};

        APP_STATE.storyboard2VideoGeneratingBySubShot = {};

        APP_STATE.storyboard2DeletingByImage = {};

        APP_STATE.storyboard2DeletingByVideo = {};

        APP_STATE.storyboard2ShotEditorState = null;

        APP_STATE.storyboard2SubShotSubjectEditorState = null;

        APP_STATE.storyboard2SavingSceneBySubShot = {};

        closeStoryboard2SubShotSubjectModal();



        const [episodeResponse, shotsResponse] = await Promise.all([

            apiRequest(`/api/episodes/${APP_STATE.currentEpisode}`),

            apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/shots`)

        ]);



        APP_STATE.currentEpisodeInfo = await episodeResponse.json();

        APP_STATE.shots = await shotsResponse.json();

        APP_STATE.currentStoryboardBoard = 'storyboard2';

        APP_STATE.storyboard2BatchGenerating = Boolean(APP_STATE.currentEpisodeInfo?.batch_generating_storyboard2_prompts);



        container.innerHTML = `

            <div class="storyboard-layout">

                <div id="storyboard2Container" class="storyboard2-container storyboard2-standalone"></div>

            </div>

        `;



        await loadStoryboard2BoardData();

        renderStoryboard2Layout();

        const state = ensureStoryboard2EpisodeState();

        if (state && storyboard2HasProcessingRows(state.boardData)) {

            startStoryboard2GenerationPolling();

        } else {

            stopStoryboard2GenerationPolling();

        }

        if (APP_STATE.currentEpisodeInfo?.batch_generating_storyboard2_prompts) {

            startVideoStatusPolling();

        }

    } catch (error) {

        console.error('Failed to load storyboard2:', error);

        container.innerHTML = '<div class="empty-state">加载失败</div>';

    }

}



async function loadStoryboard2BoardData() {

    const episodeId = APP_STATE.currentEpisode;

    if (!episodeId) {

        return null;

    }



    const response = await apiRequest(`/api/episodes/${episodeId}/storyboard2?initialize_if_empty=true`);

    let payload = null;

    try {

        payload = await response.json();

    } catch (error) {

        payload = null;

    }



    if (!response.ok) {

        throw new Error(payload?.detail || '故事板2数据加载失败');

    }



    if (!APP_STATE.storyboard2MockByEpisode) {

        APP_STATE.storyboard2MockByEpisode = {};

    }

    APP_STATE.storyboard2MockByEpisode[String(episodeId)] = payload && typeof payload === 'object'

        ? payload

        : { episode_id: episodeId, shots: [] };



    // 清理本地“生成中”残留标记，避免服务重启后前端仍显示生成中。

    const board = APP_STATE.storyboard2MockByEpisode[String(episodeId)];

    const processingImageIds = new Set();

    const processingVideoIds = new Set();

    (board?.shots || []).forEach(shot => {

        (shot?.sub_shots || []).forEach(row => {

            const rowId = Number(row?.id);

            if (!Number.isInteger(rowId) || rowId <= 0) {

                return;

            }

            const imageStatus = String(row?.image_generate_status || '').toLowerCase();

            if (imageStatus === 'processing') {

                processingImageIds.add(rowId);

            }

            const videoStatus = String(row?.video_generate_status || '').toLowerCase();

            if (videoStatus === 'submitted' || videoStatus === 'pending' || videoStatus === 'processing') {

                processingVideoIds.add(rowId);

            }

        });

    });



    APP_STATE.storyboard2GeneratingBySubShot = APP_STATE.storyboard2GeneratingBySubShot || {};

    Object.keys(APP_STATE.storyboard2GeneratingBySubShot).forEach(key => {

        const rowId = Number(key);

        if (!processingImageIds.has(rowId)) {

            delete APP_STATE.storyboard2GeneratingBySubShot[key];

        }

    });



    APP_STATE.storyboard2VideoGeneratingBySubShot = APP_STATE.storyboard2VideoGeneratingBySubShot || {};

    Object.keys(APP_STATE.storyboard2VideoGeneratingBySubShot).forEach(key => {

        const rowId = Number(key);

        if (!processingVideoIds.has(rowId)) {

            delete APP_STATE.storyboard2VideoGeneratingBySubShot[key];

        }

    });



    return APP_STATE.storyboard2MockByEpisode[String(episodeId)];

}



async function refreshStoryboard2BoardDataAndRender(scrollState = null) {

    const preservedScrollState = scrollState || captureStoryboard2ScrollState();

    await loadStoryboard2BoardData();

    renderStoryboard2Layout();

    restoreStoryboard2ScrollState(preservedScrollState);



    const state = ensureStoryboard2EpisodeState();

    if (state && storyboard2HasProcessingRows(state.boardData)) {

        startStoryboard2GenerationPolling();

    } else {

        stopStoryboard2GenerationPolling();

    }

}



function captureStoryboard2ScrollState() {

    const container = document.getElementById('storyboard2Container');

    const rowsContainer = container?.querySelector('.storyboard2-rows');

    const docScrollTop = document.documentElement?.scrollTop || document.body?.scrollTop || 0;



    return {

        windowScrollY: Number(window.scrollY ?? docScrollTop) || 0,

        rowsScrollTop: rowsContainer ? rowsContainer.scrollTop : null

    };

}



function restoreStoryboard2ScrollState(scrollState) {

    if (!scrollState) {

        return;

    }



    const apply = () => {

        const container = document.getElementById('storyboard2Container');

        const rowsContainer = container?.querySelector('.storyboard2-rows');

        if (rowsContainer && Number.isFinite(scrollState.rowsScrollTop)) {

            const maxRowsScrollTop = Math.max(0, rowsContainer.scrollHeight - rowsContainer.clientHeight);

            rowsContainer.scrollTop = Math.min(scrollState.rowsScrollTop, maxRowsScrollTop);

            const state = ensureStoryboard2EpisodeState();

            if (state?.uiState) {

                state.uiState.scrollTop = rowsContainer.scrollTop;

            }

        }



        if (Number.isFinite(scrollState.windowScrollY)) {

            window.scrollTo({

                top: Math.max(0, scrollState.windowScrollY),

                left: 0,

                behavior: 'auto'

            });

        }

    };



    apply();

    requestAnimationFrame(apply);

}



function storyboard2HasProcessingRows(boardData) {

    const isVideoProcessing = (status) => {

        const normalized = String(status || '').toLowerCase();

        return normalized === 'submitted' || normalized === 'pending' || normalized === 'processing';

    };



    const shots = boardData?.shots || [];

    return shots.some(shot => (shot?.sub_shots || []).some(

        row => (

            (row?.image_generate_status || '').toLowerCase() === 'processing'

            || isVideoProcessing(row?.video_generate_status)

            || (row?.videos || []).some(video => isVideoProcessing(video?.status))

        )

    ));

}



function startStoryboard2GenerationPolling() {

    if (APP_STATE.storyboard2GenerationPollingInterval) {

        return;

    }



    APP_STATE.storyboard2GenerationPollingInterval = setInterval(async () => {

        await pollStoryboard2GenerationStatus();

    }, STORYBOARD2_GENERATION_POLL_INTERVAL_MS);



    pollStoryboard2GenerationStatus();

}



function stopStoryboard2GenerationPolling() {

    if (APP_STATE.storyboard2GenerationPollingInterval) {

        clearInterval(APP_STATE.storyboard2GenerationPollingInterval);

        APP_STATE.storyboard2GenerationPollingInterval = null;

    }

}



async function pollStoryboard2GenerationStatus() {

    if (APP_STATE.currentStep !== 5 || !APP_STATE.currentEpisode) {

        stopStoryboard2GenerationPolling();

        return;

    }



    await withPollingGuard('storyboard2GenerationStatus', async () => {

        try {

            const pollScrollState = captureStoryboard2ScrollState();

            await loadStoryboard2BoardData();

            const state = ensureStoryboard2EpisodeState();

            const updated = state ? updateStoryboard2LayoutStatusOnly(state.boardData) : false;

            if (!updated) {

                renderStoryboard2Layout();

            }

            restoreStoryboard2ScrollState(pollScrollState);



            if (!state || !storyboard2HasProcessingRows(state.boardData)) {

                stopStoryboard2GenerationPolling();

            }

        } catch (error) {

            console.error('Failed to poll storyboard2 generation status:', error);

        }

    });

}



function ensureStoryboard2EpisodeState() {

    const episodeId = APP_STATE.currentEpisode;

    if (!episodeId) {

        return null;

    }



    if (!APP_STATE.storyboard2MockByEpisode) {

        APP_STATE.storyboard2MockByEpisode = {};

    }

    if (!APP_STATE.storyboard2UiByEpisode) {

        APP_STATE.storyboard2UiByEpisode = {};

    }



    const episodeKey = String(episodeId);

    if (!APP_STATE.storyboard2MockByEpisode[episodeKey]) {

        APP_STATE.storyboard2MockByEpisode[episodeKey] = {

            episode_id: episodeId,

            shots: []

        };

    }



    const boardData = APP_STATE.storyboard2MockByEpisode[episodeKey];

    if (!APP_STATE.storyboard2UiByEpisode[episodeKey]) {

        APP_STATE.storyboard2UiByEpisode[episodeKey] = {

            scrollTop: 0

        };

    }



    const uiState = APP_STATE.storyboard2UiByEpisode[episodeKey];



    return { episodeKey, boardData, uiState };

}



function buildStoryboard2MockFromShots(shots) {

    const allShots = Array.isArray(shots) ? [...shots] : [];

    allShots.sort((a, b) => {

        const aNumber = Number(a.shot_number) || 0;

        const bNumber = Number(b.shot_number) || 0;

        if (aNumber !== bNumber) {

            return aNumber - bNumber;

        }

        return (Number(a.variant_index) || 0) - (Number(b.variant_index) || 0);

    });



    const mainShots = allShots.filter(shot => (Number(shot.variant_index) || 0) === 0);

    const sourceShots = mainShots.length > 0 ? mainShots : allShots;



    return {

        initialized_at: new Date().toISOString(),

        shots: sourceShots.map((shot, index) => buildStoryboard2MockShot(shot, index))

    };

}



function buildStoryboard2MockShot(shot, shotIndex) {

    const timeline = parseStoryboard2Timeline(shot);

    const effectiveTimeline = timeline.length > 0

        ? timeline

        : createStoryboard2FallbackTimeline(shot?.duration);



    const shotLabel = getShotLabel(shot);

    const excerpt = (shot?.script_excerpt || shot?.scene_override || shot?.storyboard_dialogue || '').trim() || `镜头${shotLabel}原文描述`;

    const mockShotId = `s2-shot-${shot?.id || shotIndex + 1}`;



    const subShots = effectiveTimeline.map((item, index) => {

        const candidates = createStoryboard2Candidates(shotLabel, index + 1);

        return {

            id: `${mockShotId}-row-${index + 1}`,

            order: index + 1,

            time_range: getStoryboard2TimeRange(item, index, effectiveTimeline.length),

            visual_text: String(item?.visual || item?.visual_text || item?.description || `分镜${index + 1}画面描述`),

            audio_text: String(item?.audio || item?.audio_text || ''),

            candidates,

            current_image: candidates[0] ? { ...candidates[0] } : null

        };

    });



    return {

        id: mockShotId,

        source_shot_id: shot?.id || null,

        shot_label: shotLabel,

        excerpt,

        sub_shots: subShots

    };

}



function parseStoryboard2Timeline(shot) {

    if (!shot || !shot.timeline_json) {

        return [];

    }



    try {

        const rawTimeline = typeof shot.timeline_json === 'string'

            ? JSON.parse(shot.timeline_json)

            : shot.timeline_json;



        if (Array.isArray(rawTimeline)) {

            return rawTimeline;

        }

        if (rawTimeline && Array.isArray(rawTimeline.timeline)) {

            return rawTimeline.timeline;

        }

    } catch (error) {

        console.warn('[storyboard2] timeline_json parse failed:', error);

    }



    return [];

}



function createStoryboard2FallbackTimeline(durationSeconds) {

    const total = Math.max(6, Number(durationSeconds) || 10);

    const segmentCount = total <= 9 ? 2 : (total <= 14 ? 3 : 4);

    const segmentLength = total / segmentCount;

    const timeline = [];



    for (let i = 0; i < segmentCount; i++) {

        const start = Math.round(i * segmentLength);

        const end = i === segmentCount - 1

            ? total

            : Math.max(start + 1, Math.round((i + 1) * segmentLength));

        timeline.push({

            time: `${start}s-${end}s`,

            visual: `镜头细化描述 ${i + 1}：补充画闈富浣撱€佹満位与运动信息`,

            audio: `闊抽说明 ${i + 1}`

        });

    }



    return timeline;

}



function getStoryboard2TimeRange(timelineItem, index, totalCount) {

    const rawTime = timelineItem?.time || timelineItem?.time_range || '';

    if (typeof rawTime === 'string' && rawTime.trim()) {

        return rawTime.trim();

    }



    const start = timelineItem?.start_time ?? timelineItem?.start ?? timelineItem?.begin;

    const end = timelineItem?.end_time ?? timelineItem?.end;

    if (start !== undefined && end !== undefined) {

        return `${start}s-${end}s`;

    }

    if (start !== undefined && timelineItem?.duration !== undefined) {

        const targetEnd = Number(start) + Number(timelineItem.duration);

        return `${start}s-${targetEnd}s`;

    }



    return `分镜 ${index + 1}/${totalCount}`;

}



function storyboard2Hash(input) {

    const text = String(input || '');

    let hash = 0;

    for (let i = 0; i < text.length; i++) {

        hash = ((hash << 5) - hash) + text.charCodeAt(i);

        hash |= 0;

    }

    return Math.abs(hash);

}



function escapeStoryboard2SvgText(text) {

    return String(text || '')

        .replace(/&/g, '&amp;')

        .replace(/</g, '&lt;')

        .replace(/>/g, '&gt;')

        .replace(/"/g, '&quot;')

        .replace(/'/g, '&apos;');

}



function createStoryboard2MockImageDataUrl(seed) {

    const hash = storyboard2Hash(seed);

    const hueA = hash % 360;

    const hueB = (hueA + 52) % 360;

    const accent = (hueA + 110) % 360;

    const safeText = escapeStoryboard2SvgText(seed);

    const svg = `

<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="576" viewBox="0 0 1024 576">

  <defs>

    <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">

      <stop offset="0%" stop-color="hsl(${hueA}, 58%, 20%)" />

      <stop offset="100%" stop-color="hsl(${hueB}, 62%, 36%)" />

    </linearGradient>

  </defs>

  <rect width="1024" height="576" fill="url(#g)" />

  <rect x="30" y="30" width="964" height="516" fill="none" stroke="hsla(${accent}, 80%, 72%, 0.45)" stroke-width="3" />

  <text x="52" y="90" font-family="Arial, sans-serif" font-size="36" font-weight="700" fill="rgba(255,255,255,0.9)">Storyboard 2</text>

  <text x="52" y="138" font-family="Arial, sans-serif" font-size="24" fill="rgba(255,255,255,0.78)">${safeText}</text>

</svg>`;

    return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;

}



function createStoryboard2Candidates(shotLabel, subShotIndex) {

    const candidates = [];

    for (let i = 1; i <= 4; i++) {

        const seed = `S${shotLabel}-R${subShotIndex}-C${i}`;

        candidates.push({

            id: `s2-candidate-${seed}`,

            label: `\u5019\u9009${i}`,

            image_url: createStoryboard2MockImageDataUrl(seed)

        });

    }

    return candidates;

}



function collectStoryboard2TableRows(boardData) {

    const tableRows = [];

    const shots = Array.isArray(boardData?.shots) ? boardData.shots : [];



    shots.forEach((shot, shotIndex) => {

        const sourceRows = Array.isArray(shot?.sub_shots) ? shot.sub_shots : [];

        const normalizedRows = sourceRows.length > 0

            ? sourceRows

            : [{

                order: 1,

                time_range: '',

                visual_text: '',

                sora_prompt: '',

                selected_card_ids: [],

                subjects: [],

                current_image: null,

                candidates: [],

                videos: [],

                image_generate_status: 'idle',

                image_generate_progress: '',

                video_generate_status: 'idle',

                video_generate_progress: 0

            }];



        normalizedRows.forEach((row, rowIndex) => {

            tableRows.push({

                shot,

                shotIndex,

                row,

                rowIndex,

                isFirstSubShot: rowIndex === 0

            });

        });

    });



    return tableRows;

}



function getStoryboard2SubShotDomKey(entry) {

    const rowId = entry?.row?.id;

    if (rowId !== undefined && rowId !== null && String(rowId).trim() !== '') {

        return `subshot-${String(rowId).trim()}`;

    }



    const shotId = entry?.shot?.id ?? entry?.shot?.source_shot_id ?? entry?.shotIndex ?? 0;

    const rowOrder = entry?.row?.order ?? (entry?.rowIndex ?? 0) + 1;

    return `shot-${shotId}-row-${rowOrder}-${entry?.rowIndex ?? 0}`;

}



function formatStoryboard2VideoStatus(status, progress) {

    const normalized = String(status || '').toLowerCase();

    if (normalized === 'submitted' || normalized === 'pending' || normalized === 'processing') {

        const safeProgress = Number(progress);

        const progressText = Number.isFinite(safeProgress) && safeProgress > 0

            ? ` ${Math.min(99, safeProgress)}%`

            : '';

        return `\u751f\u6210\u4e2d${progressText}`;

    }

    if (normalized === 'completed') {

        return '\u5df2\u5b8c\u6210';

    }

    if (normalized === 'failed' || normalized === 'cancelled') {

        return '\u5931\u8d25';

    }

    return '\u5f85\u751f\u6210';

}



function buildStoryboard2CurrentImageHtml(shotIndex, rowIndex, row) {

    const currentImage = row?.current_image;

    if (!currentImage || !currentImage.image_url) {

        return '<div class="storyboard2-image-placeholder">\u62d6\u62fd\u5019\u9009\u56fe\u5230\u8fd9\u91cc</div>';

    }



    const currentImageAspectRatio = storyboard2SizeToAspectRatio(currentImage.size || '9:16');

    return `

        <div class="storyboard2-current-image-wrap">

            <button class="storyboard2-current-image-btn"

                    style="--sb2-image-ratio: ${currentImageAspectRatio};"

                    onclick="previewStoryboard2CurrentImage(${shotIndex}, ${rowIndex})">

                <img src="${escapeHtml(currentImage.image_url)}" alt="${escapeHtml(currentImage.label || '\u5f53\u524d\u56fe')}" />

            </button>

        </div>

    `;

}



function buildStoryboard2CandidateGridHtml(shotIndex, rowIndex, row) {

    const candidates = Array.isArray(row?.candidates) ? row.candidates : [];

    if (candidates.length === 0) {

        return '<div class="storyboard2-candidates-empty">\u6682\u65e0\u5019\u9009\u56fe</div>';

    }



    return candidates.map((candidate, candidateIndex) => {

        const canDelete = candidate?.deletable !== false;

        const isDeleting = Boolean(APP_STATE.storyboard2DeletingByImage?.[candidate?.id]);

        const deleteButtonHtml = canDelete

            ? `<button class="storyboard2-candidate-delete"

                       onclick="deleteStoryboard2CandidateImage(event, ${shotIndex}, ${rowIndex}, ${candidateIndex})"

                       title="${isDeleting ? '删除中' : '删除图片'}"

                       aria-label="${isDeleting ? '删除中' : '删除图片'}"

                       ${isDeleting ? 'disabled' : ''}>

                        ${isDeleting

                            ? '<span class="storyboard2-candidate-delete-loader"></span>'

                            : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">

                                    <path d="M3 6h18"></path>

                                    <path d="M8 6V4h8v2"></path>

                                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path>

                                    <path d="M10 11v6"></path>

                                    <path d="M14 11v6"></path>

                               </svg>`

                        }

                    </button>`

            : '';



        return `

            <div class="storyboard2-candidate-card"

                 draggable="${isDeleting ? 'false' : 'true'}"

                 ondragstart="handleStoryboard2CandidateDragStart(event, ${shotIndex}, ${rowIndex}, ${candidateIndex})"

                 ondragend="handleStoryboard2CandidateDragEnd()"

                 onclick="previewStoryboard2CandidateImage(${shotIndex}, ${rowIndex}, ${candidateIndex})">

                <div class="storyboard2-candidate-thumb">

                    <img src="${escapeHtml(candidate.image_url)}" alt="${escapeHtml(candidate.label || '\u5019\u9009\u56fe')}" />

                    ${deleteButtonHtml}

                </div>

            </div>

        `;

    }).join('');

}



function buildStoryboard2VideoListHtml(shotIndex, rowIndex, row) {

    const videos = Array.isArray(row?.videos) ? row.videos : [];

    if (videos.length === 0) {

        return '<div class="storyboard2-candidates-empty">\u6682\u65e0\u89c6\u9891</div>';

    }



    return videos.map((video, index) => {

        const videoStatus = String(video?.status || '').toLowerCase();

        const statusText = formatStoryboard2VideoStatus(videoStatus, video?.progress);

        const playableUrl = String(video?.video_url || video?.thumbnail_url || '').trim();

        const hasPlayableVideo = videoStatus === 'completed' && playableUrl;

        const videoAspectRatio = String(video?.aspect_ratio || '9:16');

        const videoPreviewAspectRatio = storyboard2SizeToAspectRatio(videoAspectRatio);

        const taskIdText = String(video?.task_id || '').trim();

        const canDeleteVideo = Boolean(video?.id);

        const isDeletingVideo = Boolean(APP_STATE.storyboard2DeletingByVideo?.[video?.id]);

        const deleteVideoButtonHtml = canDeleteVideo

            ? `<button class="storyboard2-video-delete"

                       onclick="deleteStoryboard2Video(event, ${shotIndex}, ${rowIndex}, ${index})"

                       ${isDeletingVideo ? 'disabled' : ''}>${isDeletingVideo ? '\u5220\u9664\u4e2d...' : '\u5220\u9664'}</button>`

            : '';



        return `

            <div class="storyboard2-video-item">

                <div class="storyboard2-video-item-header">

                    <div class="storyboard2-video-title-line">

                        <span>\u89c6\u9891 ${index + 1}</span>

                        <span class="storyboard2-video-taskid">${taskIdText ? escapeHtml(taskIdText) : '-'}</span>

                    </div>

                    <div class="storyboard2-video-header-actions">

                        <span class="storyboard2-video-status ${videoStatus === 'completed' ? 'completed' : (videoStatus === 'failed' ? 'failed' : 'processing')}">${escapeHtml(statusText)}</span>

                        ${deleteVideoButtonHtml}

                    </div>

                </div>

                ${hasPlayableVideo

                    ? `

                        <div class="storyboard2-video-media" style="--sb2-video-ratio: ${videoPreviewAspectRatio};">

                            <video class="storyboard2-video-player" src="${escapeHtml(playableUrl)}" controls preload="metadata"></video>

                        </div>

                    `

                    : `

                        <div class="storyboard2-video-media storyboard2-video-media-placeholder" style="--sb2-video-ratio: ${videoPreviewAspectRatio};">

                            <div class="storyboard2-video-placeholder">${escapeHtml(videoStatus === 'failed' ? (video?.error_message || '\u89c6\u9891\u751f\u6210\u5931\u8d25') : '\u89c6\u9891\u751f\u6210\u4e2d...')}</div>

                        </div>

                    `

                }

            </div>

        `;

    }).join('');

}



function getStoryboard2RowGenerationState(row) {

    const backendGenerating = String(row?.image_generate_status || '').toLowerCase() === 'processing';

    const localGenerating = Boolean(APP_STATE.storyboard2GeneratingBySubShot?.[row?.id]);

    const isGenerating = backendGenerating || localGenerating;

    const progressLabel = row?.image_generate_progress || '1/4';



    const backendVideoGenerating = ['submitted', 'pending', 'processing'].includes(

        String(row?.video_generate_status || '').toLowerCase()

    );

    const localVideoGenerating = Boolean(APP_STATE.storyboard2VideoGeneratingBySubShot?.[row?.id]);

    const isVideoGenerating = backendVideoGenerating || localVideoGenerating;

    const videoProgressValue = Number(row?.video_generate_progress || 0);

    const videoProgressLabel = Number.isFinite(videoProgressValue) && videoProgressValue > 0

        ? ` ${Math.min(99, videoProgressValue)}%`

        : '';



    return {

        isGenerating,

        progressLabel,

        isVideoGenerating,

        videoProgressLabel

    };

}



function updateStoryboard2BatchActions(container, isBatchGenerating) {

    if (!container) {

        return;

    }



    const actions = container.querySelector('.storyboard2-actions');

    if (!actions) {

        return;

    }



    const batchBtn = actions.querySelector('#batchGenerateBtn');

    if (batchBtn) {

        batchBtn.disabled = isBatchGenerating;

    }



    const existingStatus = actions.querySelector('.batch-generate-status');

    if (isBatchGenerating && !existingStatus) {

        const status = document.createElement('span');

        status.className = 'batch-generate-status';

        status.textContent = '\u6b63\u5728\u6279\u91cf\u751f\u6210\u4e2d...';

        actions.insertBefore(status, actions.firstChild);

    } else if (!isBatchGenerating && existingStatus) {

        existingStatus.remove();

    }

}



function updateStoryboard2LayoutStatusOnly(boardData) {

    const container = document.getElementById('storyboard2Container');

    if (!container) {

        return false;

    }



    const rowsContainer = container.querySelector('.storyboard2-rows');

    if (!rowsContainer) {

        return false;

    }



    const tableRows = collectStoryboard2TableRows(boardData);

    if (tableRows.length === 0) {

        return false;

    }



    const domRows = Array.from(rowsContainer.querySelectorAll('.storyboard2-row'));

    if (domRows.length !== tableRows.length) {

        return false;

    }



    const isBatchGenerating = Boolean(

        APP_STATE.currentEpisodeInfo?.batch_generating_storyboard2_prompts || APP_STATE.storyboard2BatchGenerating

    );

    updateStoryboard2BatchActions(container, isBatchGenerating);



    for (let i = 0; i < tableRows.length; i++) {

        const entry = tableRows[i];

        const domRow = domRows[i];

        const expectedKey = getStoryboard2SubShotDomKey(entry);

        const existingKey = domRow.getAttribute('data-sb2-row-key');

        if (existingKey && existingKey !== expectedKey) {

            return false;

        }



        domRow.setAttribute('data-sb2-row-key', expectedKey);

        domRow.setAttribute('data-sb2-shot-index', String(entry.shotIndex));

        domRow.setAttribute('data-sb2-row-index', String(entry.rowIndex));



        const videoList = domRow.querySelector('.storyboard2-video-list');

        const currentDropzone = domRow.querySelector('.storyboard2-current-dropzone');

        const candidateGrid = domRow.querySelector('.storyboard2-candidate-grid');

        const imageBtn = domRow.querySelector('.storyboard2-row-image-btn');

        const videoBtn = domRow.querySelector('.storyboard2-row-video-btn');

        if (!videoList || !currentDropzone || !candidateGrid || !imageBtn || !videoBtn) {

            return false;

        }



        const previousVideoScrollTop = videoList.scrollTop;

        const previousCandidateScrollTop = candidateGrid.scrollTop;



        videoList.innerHTML = buildStoryboard2VideoListHtml(entry.shotIndex, entry.rowIndex, entry.row);

        currentDropzone.innerHTML = buildStoryboard2CurrentImageHtml(entry.shotIndex, entry.rowIndex, entry.row);

        candidateGrid.innerHTML = buildStoryboard2CandidateGridHtml(entry.shotIndex, entry.rowIndex, entry.row);



        if (previousVideoScrollTop > 0) {

            const maxVideoScrollTop = Math.max(0, videoList.scrollHeight - videoList.clientHeight);

            videoList.scrollTop = Math.min(previousVideoScrollTop, maxVideoScrollTop);

        }

        if (previousCandidateScrollTop > 0) {

            const maxCandidateScrollTop = Math.max(0, candidateGrid.scrollHeight - candidateGrid.clientHeight);

            candidateGrid.scrollTop = Math.min(previousCandidateScrollTop, maxCandidateScrollTop);

        }



        const rowGenerationState = getStoryboard2RowGenerationState(entry.row);

        imageBtn.disabled = rowGenerationState.isGenerating;

        imageBtn.textContent = rowGenerationState.isGenerating

            ? `\u751f\u6210\u4e2d...${rowGenerationState.progressLabel}`

            : '\u751f\u6210\u955c\u5934\u56fe';



        videoBtn.disabled = rowGenerationState.isVideoGenerating;

        videoBtn.textContent = rowGenerationState.isVideoGenerating

            ? `\u89c6\u9891\u751f\u6210\u4e2d...${rowGenerationState.videoProgressLabel}`

            : '\u751f\u6210\u89c6\u9891';

    }



    refreshShotImageSizeButtonLabels();

    adjustStoryboard2CandidateGridViewport(container);

    bindStoryboard2CandidateGridImageLoad(container);

    return true;

}



function renderStoryboard2Layout() {

    const container = document.getElementById('storyboard2Container');

    if (!container) {

        return;

    }



    const state = ensureStoryboard2EpisodeState();

    if (!state) {

        container.innerHTML = '<div class="storyboard2-empty">\u8bf7\u5148\u9009\u62e9\u5267\u96c6</div>';

        return;

    }



    const previousRowsContainer = container.querySelector('.storyboard2-rows');

    const preservedScrollTop = previousRowsContainer

        ? previousRowsContainer.scrollTop

        : (state.uiState?.scrollTop || 0);



    const { boardData, uiState } = state;

    if (!boardData.shots || boardData.shots.length === 0) {

        container.innerHTML = '<div class="storyboard2-empty">\u6682\u65e0\u955c\u5934\u53ef\u7528\u4e8e\u6545\u4e8b\u677f2\u9884\u89c8</div>';

        return;

    }



    const tableRows = collectStoryboard2TableRows(boardData);



    const isBatchGenerating = Boolean(

        APP_STATE.currentEpisodeInfo?.batch_generating_storyboard2_prompts || APP_STATE.storyboard2BatchGenerating

    );



    const rowsHtml = tableRows.map(entry => {

        const { shot, shotIndex, row, rowIndex, isFirstSubShot } = entry;

        const rowDisplayText = row.sora_prompt || row.visual_text || '';

        const rowSceneText = String(row?.scene_override || '');

        const rowKey = getStoryboard2SubShotDomKey(entry);

        const currentImageHtml = buildStoryboard2CurrentImageHtml(shotIndex, rowIndex, row);

        const rowSubjects = Array.isArray(row?.subjects) ? row.subjects : [];

        const rowSubjectNames = rowSubjects

            .map(subject => String(subject?.name || '').trim())

            .filter(Boolean);

        const rowSubjectText = rowSubjectNames.length > 0

            ? rowSubjectNames.join('、')

            : '未指定';



        const shotCellHtml = isFirstSubShot

            ? `

                <div onclick="openStoryboard2ShotEditor(${shotIndex})" style="cursor: pointer;">

                    <div class="storyboard2-shot-cell-main">\u955c\u5934 ${escapeHtml(shot.shot_label)}</div>

                    <div class="storyboard2-shot-cell-sub">${escapeHtml(shot.excerpt || '点击编辑镜头原文描述')}</div>

                </div>

            `

            : '';



        const candidateHtml = buildStoryboard2CandidateGridHtml(shotIndex, rowIndex, row);

        const videoHtml = buildStoryboard2VideoListHtml(shotIndex, rowIndex, row);

        const rowGenerationState = getStoryboard2RowGenerationState(row);



        return `

            <div class="storyboard2-row" data-sb2-row-key="${escapeHtml(rowKey)}" data-sb2-shot-index="${shotIndex}" data-sb2-row-index="${rowIndex}">

                <div class="storyboard2-col storyboard2-col-shot-cell ${isFirstSubShot ? '' : 'is-empty'}">

                    ${shotCellHtml}

                </div>

                <div class="storyboard2-col storyboard2-col-shot">

                    <div class="storyboard2-row-title">\u5206\u955c${row.order} · ${escapeHtml(row.time_range || '')}</div>

                    <button type="button"

                            class="storyboard2-row-subtitle storyboard2-row-subtitle-btn"

                            onclick="openStoryboard2SubShotSubjectEditor(${shotIndex}, ${rowIndex})"

                            title="点击编辑分镜主体">

                        主体：${escapeHtml(rowSubjectText)}

                    </button>

                    <div class="storyboard2-row-field">

                        <div class="storyboard2-row-field-label">分镜描述</div>

                        <textarea class="storyboard2-row-textarea"

                                  placeholder="点击输入分镜描述，点击空白处自动保存"

                                  onfocus="markStoryboard2SubShotPromptOriginal(this)"

                                  onblur="saveStoryboard2SubShotPromptOnBlur(event, ${shotIndex}, ${rowIndex})">${escapeHtml(rowDisplayText)}</textarea>

                    </div>

                    <div class="storyboard2-row-field">

                        <div class="storyboard2-row-field-label">场景描述</div>

                        <textarea class="storyboard2-row-textarea storyboard2-row-scene-textarea"

                                  placeholder="可手动编辑场景描述，失去焦点自动保存"

                                  onfocus="markStoryboard2SubShotPromptOriginal(this)"

                                  onblur="saveStoryboard2SubShotSceneOnBlur(event, ${shotIndex}, ${rowIndex})">${escapeHtml(rowSceneText)}</textarea>

                    </div>

                </div>

                <div class="storyboard2-col storyboard2-col-videos">

                    <div class="storyboard2-video-list">

                        ${videoHtml}

                    </div>

                </div>

                <div class="storyboard2-col storyboard2-col-current">

                    <div class="storyboard2-current-dropzone"

                         ondragover="handleStoryboard2CurrentDragOver(event)"

                         ondragleave="handleStoryboard2CurrentDragLeave(event)"

                         ondrop="handleStoryboard2CurrentDrop(event, ${shotIndex}, ${rowIndex})">

                        ${currentImageHtml}

                    </div>

                </div>

                <div class="storyboard2-col storyboard2-col-candidates">

                    <div class="storyboard2-candidate-grid">

                        ${candidateHtml}

                    </div>

                </div>

                <div class="storyboard2-col storyboard2-col-actions">

                    <button class="secondary-button storyboard2-row-btn storyboard2-row-image-btn"

                            onclick="triggerStoryboard2RowImageGeneration(${shotIndex}, ${rowIndex})"

                            ${rowGenerationState.isGenerating ? 'disabled' : ''}>

                        ${rowGenerationState.isGenerating ? `\u751f\u6210\u4e2d...${escapeHtml(rowGenerationState.progressLabel)}` : '\u751f\u6210\u955c\u5934\u56fe'}

                    </button>

                    <button class="secondary-button storyboard2-row-btn storyboard2-row-video-btn"

                            onclick="triggerStoryboard2RowVideoGeneration(${shotIndex}, ${rowIndex})"

                            ${rowGenerationState.isVideoGenerating ? 'disabled' : ''}>

                        ${rowGenerationState.isVideoGenerating ? `\u89c6\u9891\u751f\u6210\u4e2d...${escapeHtml(rowGenerationState.videoProgressLabel)}` : '\u751f\u6210\u89c6\u9891'}

                    </button>

                </div>

            </div>

        `;

    }).join('');



    container.innerHTML = `

        <div class="storyboard2-shell">

            <div class="storyboard2-actions">

                ${isBatchGenerating ? '<span class="batch-generate-status">正在批量生成中...</span>' : ''}

                <button class="secondary-button storyboard-tool-button"

                        id="batchGenerateBtn"

                        onclick="batchGenerateStoryboard2SoraPrompts()"

                        ${isBatchGenerating ? 'disabled' : ''}>批量生成作图提示词</button>

                <button class="secondary-button storyboard-tool-button" onclick="batchGenerateStoryboard2ImageShots()">批量生成镜头图</button>

                <button class="secondary-button storyboard-tool-button" onclick="batchGenerateStoryboard2Videos()">批量生成视频</button>

                <button class="secondary-button storyboard-tool-button" onclick="batchDownloadStoryboard2Videos()">批量下载视频</button>

                <button class="secondary-button storyboard-tool-button" onclick="batchDownloadStoryboard2Images()">批量下载图片</button>

                <button class="secondary-button storyboard-tool-button" data-shot-image-size-btn="1" onclick="openShotImageSizeSettingModal()">图/视频设置</button>

                <button class="secondary-button storyboard-tool-button" onclick="editSoraPromptStyle()">Sora提示词设置</button>

                <button class="secondary-button storyboard-tool-button" onclick="editStoryboard2ImagePromptPrefix()">镜头图提示词设置</button>

            </div>

            <div class="storyboard2-main storyboard2-main-stream">

                <section class="storyboard2-right-panel storyboard2-stream-panel">

                    <div class="storyboard2-table-header">

                        <div>\u955c\u5934</div>

                        <div>\u5206\u955c\u63cf\u8ff0</div>

                        <div>视频区</div>

                        <div>\u5f53\u524d\u56fe\u7247\u533a</div>

                        <div>\u53ef\u9009\u56fe\u7247\u533a</div>

                        <div>\u64cd\u4f5c\u6309\u94ae\u533a</div>

                    </div>

                    <div class="storyboard2-rows">

                        ${rowsHtml || '<div class="storyboard2-empty">\u6682\u65e0\u53ef\u5c55\u793a\u7684\u5206\u955c\u6570\u636e</div>'}

                    </div>

                </section>

            </div>

        </div>

    `;

    refreshShotImageSizeButtonLabels();



    const rowsContainer = container.querySelector('.storyboard2-rows');

    if (rowsContainer) {

        rowsContainer.scrollTop = preservedScrollTop;

        uiState.scrollTop = rowsContainer.scrollTop;

        rowsContainer.addEventListener('scroll', () => {

            uiState.scrollTop = rowsContainer.scrollTop;

        }, { passive: true });

    }



    adjustStoryboard2CandidateGridViewport(container);

    bindStoryboard2CandidateGridImageLoad(container);

    ensureStoryboard2ViewportResizeListener();

}



let storyboard2ViewportResizeBound = false;

function ensureStoryboard2ViewportResizeListener() {

    if (storyboard2ViewportResizeBound) {

        return;

    }

    storyboard2ViewportResizeBound = true;

    window.addEventListener('resize', () => {

        const container = document.getElementById('storyboard2Container');

        if (!container) {

            return;

        }

        adjustStoryboard2CandidateGridViewport(container);

    }, { passive: true });

}



function adjustStoryboard2CandidateGridViewport(root = document) {

    const grids = root.querySelectorAll('.storyboard2-candidate-grid');

    grids.forEach(grid => {

        const cards = Array.from(grid.querySelectorAll('.storyboard2-candidate-card'));

        if (cards.length <= 4) {

            grid.style.maxHeight = '';

            return;

        }



        const visibleCards = cards.slice(0, 4);

        let minTop = Number.POSITIVE_INFINITY;

        let maxBottom = 0;

        visibleCards.forEach(card => {

            const top = card.offsetTop;

            const bottom = top + card.offsetHeight;

            if (top < minTop) {

                minTop = top;

            }

            if (bottom > maxBottom) {

                maxBottom = bottom;

            }

        });



        if (!Number.isFinite(minTop) || maxBottom <= minTop) {

            grid.style.maxHeight = '';

            return;

        }



        grid.style.maxHeight = `${Math.ceil(maxBottom - minTop)}px`;

    });

}



function bindStoryboard2CandidateGridImageLoad(root = document) {

    const images = root.querySelectorAll('.storyboard2-candidate-grid img');

    images.forEach(img => {

        if (img.dataset.storyboard2ResizeBound === '1') {

            return;

        }

        img.dataset.storyboard2ResizeBound = '1';



        const scheduleResize = () => {

            requestAnimationFrame(() => {

                adjustStoryboard2CandidateGridViewport(root);

            });

        };



        if (img.complete) {

            scheduleResize();

            return;

        }



        img.addEventListener('load', scheduleResize, { once: true });

        img.addEventListener('error', scheduleResize, { once: true });

    });

}



async function batchGenerateStoryboard2SoraPrompts() {

    const state = ensureStoryboard2EpisodeState();

    if (!state || !state.boardData || !Array.isArray(state.boardData.shots) || state.boardData.shots.length === 0) {

        showToast('没有可生成的镜头', 'info');

        return;

    }



    await showStoryboard2BatchGenerateModal();

}



async function showStoryboard2BatchGenerateModal() {

    const state = ensureStoryboard2EpisodeState();

    if (!state) {

        return;

    }



    const modal = document.getElementById('batchGenerateModal');

    const templateSelect = document.getElementById('batchTemplateSelect');

    const confirmBtn = document.getElementById('batchGenerateConfirm');

    const shotsList = document.getElementById('batchShotsList');



    if (!modal || !confirmBtn || !shotsList) {

        showToast('批量生成弹窗未初始化', 'error');

        return;

    }



    if (templateSelect) {

        if (!Array.isArray(APP_STATE.templates) || APP_STATE.templates.length === 0) {

            try {

                const templatesResponse = await apiRequest('/api/templates');

                if (templatesResponse && templatesResponse.ok) {

                    APP_STATE.templates = await templatesResponse.json();

                }

            } catch (error) {

                console.error('Failed to load templates for storyboard2 batch modal:', error);

            }

        }



        if (Array.isArray(APP_STATE.templates) && APP_STATE.templates.length > 0) {

            templateSelect.innerHTML = APP_STATE.templates.map(t =>

                `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)}</option>`

            ).join('');



            const defaultTemplate = APP_STATE.templates.find(t => t.name.includes('2d漫画风格（细）'))

                || APP_STATE.templates[0];

            if (defaultTemplate) {

                templateSelect.value = defaultTemplate.name;

            }

        }

    }



    const storyboard2Shots = (state.boardData.shots || []);

    shotsList.innerHTML = storyboard2Shots.map(shot => `

        <label style="display: flex; align-items: center; gap: 8px; padding: 6px; cursor: pointer; border-radius: 2px; transition: background 0.2s;"

               onmouseover="this.style.background='#1a1a1a'"

               onmouseout="this.style.background='transparent'">

            <input type="checkbox"

                   class="batch-shot-checkbox"

                   data-shot-id="${shot.id}"

                   checked

                   style="width: 16px; height: 16px; cursor: pointer;">

            <span style="color: #fff; font-size: 13px;">镜头 ${escapeHtml(shot.shot_label || '')}</span>

        </label>

    `).join('');



    const newConfirmBtn = confirmBtn.cloneNode(true);

    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);



    newConfirmBtn.addEventListener('click', async () => {

        const template = templateSelect ? templateSelect.value : '';

        const selectedCheckboxes = document.querySelectorAll('.batch-shot-checkbox:checked');

        const selectedShotIds = Array.from(selectedCheckboxes).map(cb => parseInt(cb.dataset.shotId, 10));



        if (selectedShotIds.length === 0) {

            showToast('请至少选择一个镜头', 'warning');

            return;

        }



        closeBatchGenerateModal();

        showToast(`正在为 ${selectedShotIds.length} 个镜头提交批量生成任务...`, 'info');



        try {

            const requestBody = {

                shot_ids: selectedShotIds

            };

            if (template) {

                requestBody.default_template = template;

            }



            const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/storyboard2/batch-generate-sora-prompts`, {

                method: 'POST',

                body: JSON.stringify(requestBody)

            });



            if (response && response.ok) {

                const result = await response.json();

                APP_STATE.storyboard2BatchGenerating = true;



                const episodeResponse = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}`);

                if (episodeResponse && episodeResponse.ok) {

                    APP_STATE.currentEpisodeInfo = await episodeResponse.json();

                }



                await refreshStoryboard2BoardDataAndRender();

                startVideoStatusPolling();

                showToast(result.message || '故事板2批量生成任务已开始，请稍后查看结果', 'success');

            } else if (response) {

                const error = await response.json();

                const detail = error.detail || '未知错误';

                if (shouldShowStoryboardVideoWaitDialog(detail)) {

                    showAlertDialog(detail);

                } else {

                    showToast(`批量生成失败: ${detail}`, 'error');

                }

            }

        } catch (error) {

            console.error('Failed to batch generate storyboard2 sora prompts:', error);

            showToast('批量生成失败', 'error');

        }

    });



    const clickHandler = (e) => {

        if (e.target === modal) {

            closeBatchGenerateModal();

            modal.removeEventListener('click', clickHandler);

        }

    };

    modal.addEventListener('click', clickHandler);



    modal.classList.add('active');

}



function closeStoryboard2ShotEditorModal() {

    const modal = document.getElementById('storyboard2ShotEditorModal');

    if (modal) {

        modal.remove();

    }

    APP_STATE.storyboard2ShotEditorState = null;

}



function normalizeStoryboard2SelectedCardIds(rawValue) {

    if (!Array.isArray(rawValue)) {

        return [];

    }



    const normalized = [];

    const seen = new Set();

    rawValue.forEach(item => {

        const cardId = Number(item);

        if (!Number.isInteger(cardId) || cardId <= 0 || seen.has(cardId)) {

            return;

        }

        seen.add(cardId);

        normalized.push(cardId);

    });

    return normalized;

}



function closeStoryboard2SubShotSubjectModal() {

    const modal = document.getElementById('storyboard2SubShotSubjectModal');

    if (modal) {

        modal.remove();

    }

    APP_STATE.storyboard2SubShotSubjectEditorState = null;

}



function renderStoryboard2SubShotSubjectEditorSubjects() {

    const container = document.getElementById('storyboard2SubShotSubjectEditorSubjects');

    const editorState = APP_STATE.storyboard2SubShotSubjectEditorState;

    if (!container || !editorState) {

        return;

    }



    const subjects = Array.isArray(editorState.subjects) ? editorState.subjects : [];

    const selectedSet = new Set(normalizeStoryboard2SelectedCardIds(editorState.selectedCardIds));

    if (subjects.length === 0) {

        container.innerHTML = '<div class="storyboard-empty-state">暂无主体</div>';

        return;

    }



    const { characters, scenes, props } = groupSubjectCardsByType(subjects);



    const renderCards = (cardList) => cardList.map(subject => {

        const cardId = Number(subject.id);

        const previewImage = subject.preview_image ? getImageUrl(subject.preview_image) : '';

        const previewHtml = previewImage

            ? `<img class="storyboard-subject-image" src="${escapeHtml(previewImage)}" alt="${escapeHtml(subject.name || '')}">`

            : '<div class="storyboard-subject-placeholder">NO IMAGE</div>';

        const aliasText = subject.alias ? `<span style="color:#777;"> · ${escapeHtml(subject.alias)}</span>` : '';

        return `

            <div class="storyboard-subject-card ${selectedSet.has(cardId) ? 'selected' : ''}"

                 onclick="toggleStoryboard2SubShotSubject(${cardId})">

                <div class="storyboard-subject-thumb">${previewHtml}</div>

                <div class="storyboard-subject-info">

                    <div class="storyboard-subject-name">${escapeHtml(subject.name || '')}${aliasText}</div>

                    <div class="storyboard-subject-type">${escapeHtml(subject.card_type || '')}</div>

                </div>

            </div>

        `;

    }).join('');



    let html = '';

    if (characters.length > 0) {

        html += `

            <div class="subject-type-group">

                <div class="subject-type-label">角色</div>

                <div class="storyboard-subject-grid">${renderCards(characters)}</div>

            </div>

        `;

    }

    if (scenes.length > 0) {

        html += `

            <div class="subject-type-group">

                <div class="subject-type-label">场景</div>

                <div class="storyboard-subject-grid">${renderCards(scenes)}</div>

            </div>

        `;

    }



    container.innerHTML = html || '<div class="storyboard-empty-state">暂无主体</div>';

}



function toggleStoryboard2SubShotSubject(cardId) {

    const editorState = APP_STATE.storyboard2SubShotSubjectEditorState;

    if (!editorState) {

        return;

    }



    const resolvedCardId = Number(cardId);

    if (!Number.isInteger(resolvedCardId) || resolvedCardId <= 0) {

        return;

    }



    const selectedCardIds = normalizeStoryboard2SelectedCardIds(editorState.selectedCardIds);

    const existingIndex = selectedCardIds.indexOf(resolvedCardId);

    if (existingIndex >= 0) {

        selectedCardIds.splice(existingIndex, 1);

    } else {

        selectedCardIds.push(resolvedCardId);

    }



    editorState.selectedCardIds = selectedCardIds;

    renderStoryboard2SubShotSubjectEditorSubjects();

}



async function openStoryboard2SubShotSubjectEditor(shotIndex, rowIndex) {

    try {

        await loadStoryboard2BoardData();

    } catch (error) {

        console.error('Failed to refresh storyboard2 data before opening sub-shot subject editor:', error);

    }



    const state = ensureStoryboard2EpisodeState();

    if (!state) {

        return;

    }



    const shot = state.boardData.shots?.[shotIndex];

    const row = shot?.sub_shots?.[rowIndex];

    if (!shot || !row || !row.id) {

        showToast('分镜数据不存在，无法编辑主体', 'error');

        return;

    }



    closeStoryboard2SubShotSubjectModal();



    const availableSubjects = Array.isArray(state.boardData.available_subjects)

        ? state.boardData.available_subjects

        : [];

    const selectedCardIds = normalizeStoryboard2SelectedCardIds(

        Array.isArray(row.selected_card_ids) && row.selected_card_ids.length > 0

            ? row.selected_card_ids

            : shot.selected_card_ids

    );



    APP_STATE.storyboard2SubShotSubjectEditorState = {

        shotIndex,

        rowIndex,

        shotId: shot.id,

        subShotId: row.id,

        shotLabel: shot.shot_label || shot.shot_number || '',

        rowOrder: row.order || '',

        timeRange: row.time_range || '',

        subjects: availableSubjects,

        selectedCardIds,

        saving: false

    };



    const modal = document.createElement('div');

    modal.className = 'modal active storyboard2-subshot-subject-modal';

    modal.id = 'storyboard2SubShotSubjectModal';

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="closeStoryboard2SubShotSubjectModal()"></div>

        <div class="modal-content" style="max-width: 900px; width: 94vw; max-height: calc(100vh - 48px); padding: 16px; background: #0f0f0f; border: 1px solid #2a2a2a; display: flex; flex-direction: column;">

            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">

                <div>

                    <div style="font-size:16px; color:#fff; font-weight:600;">编辑分镜主体</div>

                    <div style="margin-top:4px; font-size:12px; color:#8a8a8a;">镜头 ${escapeHtml(String(shot.shot_label || ''))} · 分镜${escapeHtml(String(row.order || ''))}${row.time_range ? ` · ${escapeHtml(row.time_range)}` : ''}</div>

                </div>

                <button class="modal-close" onclick="closeStoryboard2SubShotSubjectModal()">&times;</button>

            </div>



            <div style="flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden; padding-right: 4px;">

                <div id="storyboard2SubShotSubjectEditorSubjects"></div>

            </div>



            <div style="display:flex; justify-content:flex-end; gap:8px; margin-top: 10px;">

                <button class="secondary-button" onclick="closeStoryboard2SubShotSubjectModal()">取消</button>

                <button class="primary-button" id="storyboard2SubShotSubjectSaveBtn" onclick="saveStoryboard2SubShotSubjects()">保存</button>

            </div>

        </div>

    `;



    document.body.appendChild(modal);

    renderStoryboard2SubShotSubjectEditorSubjects();

}



async function saveStoryboard2SubShotSubjects() {

    const editorState = APP_STATE.storyboard2SubShotSubjectEditorState;

    if (!editorState || !editorState.subShotId) {

        return;

    }

    if (editorState.saving) {

        return;

    }



    const saveBtn = document.getElementById('storyboard2SubShotSubjectSaveBtn');

    editorState.saving = true;

    if (saveBtn) {

        saveBtn.disabled = true;

        saveBtn.textContent = '保存中...';

    }



    try {

        const selectedCardIds = normalizeStoryboard2SelectedCardIds(editorState.selectedCardIds);

        const scrollState = captureStoryboard2ScrollState();



        const response = await apiRequest(`/api/storyboard2/subshots/${editorState.subShotId}`, {

            method: 'PATCH',

            body: JSON.stringify({

                selected_card_ids: selectedCardIds

            })

        });



        let payload = null;

        try {

            payload = await response.json();

        } catch (error) {

            payload = null;

        }



        if (!response.ok) {

            throw new Error(payload?.detail || '分镜主体保存失败');

        }



        closeStoryboard2SubShotSubjectModal();

        await refreshStoryboard2BoardDataAndRender(scrollState);

        showToast('分镜主体已保存', 'success');

    } catch (error) {

        console.error('Failed to save storyboard2 sub-shot subjects:', error);

        showToast(`保存失败: ${error.message}`, 'error');

    } finally {

        if (APP_STATE.storyboard2SubShotSubjectEditorState) {

            APP_STATE.storyboard2SubShotSubjectEditorState.saving = false;

        }

        if (saveBtn) {

            saveBtn.disabled = false;

            saveBtn.textContent = '保存';

        }

    }

}



function renderStoryboard2ShotEditorSubjects() {

    const container = document.getElementById('storyboard2ShotEditorSubjects');

    const editorState = APP_STATE.storyboard2ShotEditorState;

    if (!container || !editorState) {

        return;

    }



    const subjects = Array.isArray(editorState.subjects) ? editorState.subjects : [];

    const selectedSet = new Set(normalizeStoryboard2SelectedCardIds(editorState.selectedCardIds));

    if (subjects.length === 0) {

        container.innerHTML = '<div class="storyboard-empty-state">暂无主体</div>';

        return;

    }



    const { characters, scenes, props } = groupSubjectCardsByType(subjects);



    const renderCards = (cardList) => cardList.map(subject => {

        const cardId = Number(subject.id);

        const previewImage = subject.preview_image ? getImageUrl(subject.preview_image) : '';

        const previewHtml = previewImage

            ? `<img class="storyboard-subject-image" src="${escapeHtml(previewImage)}" alt="${escapeHtml(subject.name || '')}">`

            : '<div class="storyboard-subject-placeholder">NO IMAGE</div>';

        const aliasText = subject.alias ? `<span style="color:#777;"> · ${escapeHtml(subject.alias)}</span>` : '';

        return `

            <div class="storyboard-subject-card ${selectedSet.has(cardId) ? 'selected' : ''}"

                 onclick="toggleStoryboard2ShotSubject(${cardId})">

                <div class="storyboard-subject-thumb">${previewHtml}</div>

                <div class="storyboard-subject-info">

                    <div class="storyboard-subject-name">${escapeHtml(subject.name || '')}${aliasText}</div>

                    <div class="storyboard-subject-type">${escapeHtml(subject.card_type || '')}</div>

                </div>

            </div>

        `;

    }).join('');



    let html = '';

    if (characters.length > 0) {

        html += `

            <div class="subject-type-group">

                <div class="subject-type-label">角色</div>

                <div class="storyboard-subject-grid">${renderCards(characters)}</div>

            </div>

        `;

    }

    if (scenes.length > 0) {

        html += `

            <div class="subject-type-group">

                <div class="subject-type-label">场景</div>

                <div class="storyboard-subject-grid">${renderCards(scenes)}</div>

            </div>

        `;

    }



    container.innerHTML = html || '<div class="storyboard-empty-state">暂无主体</div>';

}



function toggleStoryboard2ShotSubject(cardId) {

    const editorState = APP_STATE.storyboard2ShotEditorState;

    if (!editorState) {

        return;

    }



    const resolvedCardId = Number(cardId);

    if (!Number.isInteger(resolvedCardId) || resolvedCardId <= 0) {

        return;

    }



    const selectedCardIds = normalizeStoryboard2SelectedCardIds(editorState.selectedCardIds);

    const existingIndex = selectedCardIds.indexOf(resolvedCardId);

    if (existingIndex >= 0) {

        selectedCardIds.splice(existingIndex, 1);

    } else {

        selectedCardIds.push(resolvedCardId);

    }



    editorState.selectedCardIds = selectedCardIds;

    renderStoryboard2ShotEditorSubjects();

}



async function openStoryboard2ShotEditor(shotIndex) {

    closeStoryboard2SubShotSubjectModal();



    try {

        await loadStoryboard2BoardData();

    } catch (error) {

        console.error('Failed to refresh storyboard2 data before opening shot editor:', error);

    }



    const state = ensureStoryboard2EpisodeState();

    if (!state) {

        return;

    }



    const shot = state.boardData.shots?.[shotIndex];

    if (!shot) {

        return;

    }



    closeStoryboard2ShotEditorModal();



    APP_STATE.storyboard2ShotEditorState = {

        shotId: shot.id

    };



    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'storyboard2ShotEditorModal';

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="closeStoryboard2ShotEditorModal()"></div>

        <div class="modal-content" style="max-width: 760px; width: 92vw; max-height: calc(100vh - 48px); padding: 16px; background: #0f0f0f; border: 1px solid #2a2a2a; display: flex; flex-direction: column;">

            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">

                <div style="font-size:16px; color:#fff; font-weight:600;">编辑镜头 ${escapeHtml(shot.shot_label || '')}</div>

                <button class="modal-close" onclick="closeStoryboard2ShotEditorModal()">&times;</button>

            </div>



            <div style="flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden; padding-right: 4px; -webkit-overflow-scrolling: touch;">

                <div style="margin-bottom: 12px;">

                    <div style="font-size:12px; color:#8d8d8d; margin-bottom:6px;">镜头原文描述</div>

                    <textarea id="storyboard2ShotExcerptInput"

                              style="width:100%; min-height:120px; resize:vertical; background:#111; color:#fff; border:1px solid #2d2d2d; border-radius:8px; padding:10px; font-size:13px; line-height:1.6;">${escapeHtml(shot.excerpt || '')}</textarea>

                </div>

            </div>



            <div style="display:flex; justify-content:flex-end; gap:8px; margin-top: 8px;">

                <button class="secondary-button" onclick="closeStoryboard2ShotEditorModal()">取消</button>

                <button class="primary-button" id="storyboard2ShotSaveBtn" onclick="saveStoryboard2ShotExcerpt(${shot.id})">保存</button>

            </div>

        </div>

    `;



    document.body.appendChild(modal);

}



async function saveStoryboard2ShotExcerpt(shotId) {

    const input = document.getElementById('storyboard2ShotExcerptInput');

    const saveBtn = document.getElementById('storyboard2ShotSaveBtn');

    if (!input) {

        return;

    }



    const excerpt = input.value || '';



    if (saveBtn) {

        saveBtn.disabled = true;

        saveBtn.textContent = '保存中...';

    }



    try {

        const response = await apiRequest(`/api/storyboard2/shots/${shotId}`, {

            method: 'PATCH',

            body: JSON.stringify({

                excerpt

            })

        });



        let payload = null;

        try {

            payload = await response.json();

        } catch (error) {

            payload = null;

        }



        if (!response.ok) {

            throw new Error(payload?.detail || '镜头设置保存失败');

        }



        closeStoryboard2ShotEditorModal();

        await refreshStoryboard2BoardDataAndRender();

        showToast('镜头描述已保存', 'success');

    } catch (error) {

        console.error('Failed to save storyboard2 shot excerpt:', error);

        showToast(`保存失败: ${error.message}`, 'error');

    } finally {

        if (saveBtn) {

            saveBtn.disabled = false;

            saveBtn.textContent = '保存';

        }

    }

}



function normalizeShotImageSize(size) {

    const legacySizeMap = {

        '1:2': '9:16',

        '2:1': '16:9'

    };

    const normalized = legacySizeMap[size] || size;

    const allowedSizes = ['21:9', '16:9', '3:2', '4:3', '1:1', '3:4', '2:3', '9:16'];

    return allowedSizes.includes(normalized) ? normalized : '9:16';

}



function getShotImageSizeOptionsHtml(selectedSize = '9:16') {

    const sizes = ['21:9', '16:9', '3:2', '4:3', '1:1', '3:4', '2:3', '9:16'];

    const normalizedSelected = normalizeShotImageSize(selectedSize);

    return sizes.map(size => {

        const selected = size === normalizedSelected ? ' selected' : '';

        return `<option value="${size}"${selected}>${size}</option>`;

    }).join('');

}



function normalizeStoryboard2VideoDuration(duration) {

    const parsed = Number(duration);

    if (parsed === 6 || parsed === 10) {

        return parsed;

    }

    return 6;

}



function normalizeStoryboard2ImageCw(value, defaultValue = 50) {

    const fallback = Number.isFinite(Number(defaultValue)) ? Number(defaultValue) : 100;

    const parsed = Number(value);

    const normalized = Number.isFinite(parsed) ? Math.round(parsed) : Math.round(fallback);

    return Math.max(1, Math.min(100, normalized));

}



function getStoryboard2VideoDurationOptionsHtml(selectedDuration = 6) {

    const normalized = normalizeStoryboard2VideoDuration(selectedDuration);

    const options = [6, 10];

    return options.map(item => {

        const selected = item === normalized ? ' selected' : '';

        return `<option value="${item}"${selected}>${item}秒</option>`;

    }).join('');

}



function getEpisodeShotImageSize() {

    const rawSize = APP_STATE.currentEpisodeInfo?.shot_image_size;

    return normalizeShotImageSize(rawSize || '9:16');

}


function getStoryboardSoraShotImageSize() {

    const storyboardVideoSettings = getEpisodeStoryboardVideoSettings();

    return normalizeShotImageSize(
        storyboardVideoSettings?.aspect_ratio || getEpisodeShotImageSize()
    );

}



function getEpisodeStoryboard2VideoDuration() {

    const rawDuration = APP_STATE.currentEpisodeInfo?.storyboard2_video_duration;

    return normalizeStoryboard2VideoDuration(rawDuration || 6);

}



function getEpisodeStoryboard2ImageCw() {

    const rawValue = APP_STATE.currentEpisodeInfo?.storyboard2_image_cw;

    return normalizeStoryboard2ImageCw(rawValue, 50);

}



function getEpisodeStoryboard2IncludeSceneReferences() {

    return Boolean(APP_STATE.currentEpisodeInfo?.storyboard2_include_scene_references);

}



const DETAIL_IMAGES_MODEL_LABELS = {

    'seedream-4.0': 'Seedream 4.0',

    'seedream-4.1': 'Seedream 4.1',

    'seedream-4.5': 'Seedream 4.5',

    'seedream-4.6': 'Seedream 4.6',

    'nano-banana-2': 'Nano Banana 2',

    'nano-banana-pro': 'Nano Banana Pro',

    'gpt-image-2': 'GPT Image 2'

};



function normalizeDetailImagesModel(model, defaultModel = 'seedream-4.0') {

    const aliases = {

        'jimeng': 'seedream-4.0',

        'jimeng-4.0': 'seedream-4.0',

        'jimeng-4.1': 'seedream-4.1',

        'jimeng-4.5': 'seedream-4.5',

        'jimeng-4.6': 'seedream-4.6',

        'seedream-4-0': 'seedream-4.0',

        'seedream-4-1': 'seedream-4.1',

        'seedream-4-5': 'seedream-4.5',

        'seedream-4-6': 'seedream-4.6',

        'doubao-seedance-4-5': 'seedream-4.5',

        'banana2': 'nano-banana-2',

        'banana2-moti': 'nano-banana-2',

        'banana-pro': 'nano-banana-pro',

        'nano banana 2': 'nano-banana-2',

        'nano-banana-2': 'nano-banana-2',

        'nano banana pro': 'nano-banana-pro',

        'nano-banana-pro': 'nano-banana-pro',

        'gpt image 2': 'gpt-image-2',

        'gpt-image-2': 'gpt-image-2'

    };

    const raw = String(model || '').trim().toLowerCase();

    if (raw) {

        return aliases[raw] || raw;

    }

    const fallback = aliases[String(defaultModel || '').trim().toLowerCase()] || String(defaultModel || '').trim().toLowerCase();

    if (fallback) {

        return fallback;

    }

    return 'seedream-4.0';

}



function normalizeDetailImagesProvider(provider, defaultProvider = '') {

    const aliases = {

        'jimeng': 'jimeng',

        'momo': 'momo',

        'banana': 'momo',

        'moti': 'momo',

        'moapp': 'momo',

        'gettoken': 'momo'

    };

    const raw = String(provider || '').trim().toLowerCase();

    if (raw) {

        return aliases[raw] || raw;

    }

    const fallback = String(defaultProvider || '').trim().toLowerCase();

    return aliases[fallback] || fallback;

}



function getDetailImagesModelLabel(model) {

    const normalized = normalizeDetailImagesModel(model, 'seedream-4.0');

    const matchedProvider = IMAGE_MODEL_CATALOG?.providers?.find(provider => {

        return Array.isArray(provider.models) && provider.models.some(item => item.value === normalized);

    });

    const target = matchedProvider?.models?.find(item => item.value === normalized);

    return target?.label || DETAIL_IMAGES_MODEL_LABELS[normalized] || normalized;

}



function getDetailImagesModelOptionsHtml(selectedModel = 'seedream-4.0') {

    const normalizedSelected = normalizeDetailImagesModel(selectedModel, 'seedream-4.0');

    const selection = getDefaultImageSelection(IMAGE_MODEL_CATALOG, null, normalizedSelected);

    return buildImageSelectOptions(

        getImageModelsForProvider(IMAGE_MODEL_CATALOG, selection.provider),

        selection.model,

        '选择模型'

    );

}



function getEpisodeDetailImagesModel() {

    const rawModel = APP_STATE.currentEpisodeInfo?.detail_images_model;

    return normalizeDetailImagesModel(rawModel, 'seedream-4.0');

}



function getEpisodeDetailImagesProvider() {

    const rawProvider = APP_STATE.currentEpisodeInfo?.detail_images_provider;

    return normalizeDetailImagesProvider(rawProvider, '');

}


function normalizeMotiVideoAccountName(value) {

    return String(value || '').trim();

}


function getMotiVideoAccountRecords() {

    const records = APP_STATE.motiVideoProviderAccounts?.records;

    if (!Array.isArray(records)) {

        return [];

    }

    const seen = new Set();

    return records

        .map(record => ({

            ...record,

            account_id: normalizeMotiVideoAccountName(record?.account_id)

        }))

        .filter(record => {

            if (!record.account_id || seen.has(record.account_id)) {

                return false;

            }

            seen.add(record.account_id);

            return true;

        });

}


function buildMotiVideoAccountOptionsHtml(selectedAccount = '', options = {}) {

    const selected = normalizeMotiVideoAccountName(selectedAccount);

    const optionItems = [];
    const blankLabel = String(options.blankLabel || '不指定账号');
    optionItems.push(`<option value="">${escapeHtml(blankLabel)}</option>`);

    let hasSelected = !selected;

    getMotiVideoAccountRecords().forEach(record => {

        const accountName = normalizeMotiVideoAccountName(record.account_id);

        const selectedAttr = accountName === selected ? ' selected' : '';

        if (accountName === selected) {

            hasSelected = true;

        }

        optionItems.push(`<option value="${escapeHtml(accountName)}"${selectedAttr}>${escapeHtml(accountName)}</option>`);

    });

    if (!hasSelected && selected) {

        optionItems.push(`<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)}</option>`);

    }

    return optionItems.join('');

}


function getEpisodeStoryboardVideoAppointAccount() {

    return normalizeMotiVideoAccountName(APP_STATE.currentEpisodeInfo?.storyboard_video_appoint_account);

}


function getShotStoryboardVideoAppointAccount(shot = APP_STATE.currentShot) {

    return normalizeMotiVideoAccountName(shot?.storyboard_video_appoint_account);

}


async function loadMotiVideoProviderAccounts() {

    try {

        const response = await apiRequest('/api/video/providers/moti/accounts');

        if (!response || !response.ok) {

            throw new Error('Moti账号列表加载失败');

        }

        const payload = await response.json();

        APP_STATE.motiVideoProviderAccounts = payload && typeof payload === 'object'

            ? payload

            : { total: 0, records: [] };

        return APP_STATE.motiVideoProviderAccounts;

    } catch (error) {

        console.error('Failed to load Moti video accounts:', error);

        APP_STATE.motiVideoProviderAccounts = APP_STATE.motiVideoProviderAccounts || { total: 0, records: [] };

        return APP_STATE.motiVideoProviderAccounts;

    }

}


function buildStoryboardVideoGenerationRequestBody(appointAccount = getEpisodeStoryboardVideoAppointAccount()) {

    const normalizedAccount = normalizeMotiVideoAccountName(appointAccount);

    if (!normalizedAccount) {

        return {};

    }

    return {

        appoint_account: normalizedAccount

    };

}



// Load video model pricing from API

async function loadVideoModelPricing() {

    try {

        const response = await apiRequest('/api/video-model-pricing');

        const data = await response.json();

        if (data.pricing) {

            APP_STATE.videoModelPricing = data.pricing;

            console.log('Video model pricing loaded:', APP_STATE.videoModelPricing);

        }

    } catch (error) {

        console.error('Failed to load video model pricing:', error);

        // Fall back to default pricing if API fails

        APP_STATE.videoModelPricing = getDefaultVideoModelPricing();

    }

}



// Get default pricing in case API fails

function getDefaultVideoModelPricing() {

    return {

        'sora-2': {

            '10_16:9': { duration: 10, aspect_ratio: '16:9', price_yuan: 1 },

            '15_16:9': { duration: 15, aspect_ratio: '16:9', price_yuan: 2 },

            '25_16:9': { duration: 25, aspect_ratio: '16:9', price_yuan: 3 }

        },

        'grok': {

            '6_9:16': { duration: 6, aspect_ratio: '9:16', price_yuan: 0.09 },

            '10_9:16': { duration: 10, aspect_ratio: '9:16', price_yuan: 0.19 }

        }

    };

}



const DEFAULT_STORYBOARD_VIDEO_MODEL = 'Seedance 2.0 Fast';



const STORYBOARD_VIDEO_MODEL_OPTIONS = [

    { value: 'grok', label: 'grok' },

    { value: 'Seedance 2.0 Fast VIP', label: 'Seedance 2.0 Fast VIP' },

    { value: 'Seedance 2.0 Fast', label: 'Seedance 2.0 Fast' },

    { value: 'Seedance 2.0 VIP', label: 'Seedance 2.0 VIP' },

    { value: 'Seedance 2.0', label: 'Seedance 2.0' }

];



const STORYBOARD_VIDEO_MODEL_CONFIG = {

    'sora-2': {

        label: 'sora-2',

        provider: 'yijia',

        aspectRatios: ['16:9', '9:16'],

        durations: [10, 15, 25],

        defaultAspectRatio: '16:9',

        defaultDuration: 15,

        resolutionNames: [],

        defaultResolution: ''

    },

    'grok': {

        label: 'grok',

        provider: 'yijia',

        aspectRatios: ['21:9', '16:9', '3:2', '4:3', '1:1', '3:4', '2:3', '9:16'],

        durations: [10, 20, 30],

        defaultAspectRatio: '9:16',

        defaultDuration: 10,

        resolutionNames: ['480p', '720p'],

        defaultResolution: '720p'

    },

    'Seedance 2.0 Fast VIP': {

        label: 'Seedance 2.0 Fast VIP',

        provider: 'moti',

        aspectRatios: ['21:9', '16:9', '4:3', '1:1', '3:4', '9:16'],

        durations: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],

        defaultAspectRatio: '16:9',

        defaultDuration: 10,

        resolutionNames: [],

        defaultResolution: ''

    },

    'Seedance 2.0 Fast': {

        label: 'Seedance 2.0 Fast',

        provider: 'moti',

        aspectRatios: ['21:9', '16:9', '4:3', '1:1', '3:4', '9:16'],

        durations: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],

        defaultAspectRatio: '16:9',

        defaultDuration: 10,

        resolutionNames: [],

        defaultResolution: ''

    },

    'Seedance 2.0 VIP': {

        label: 'Seedance 2.0 VIP',

        provider: 'moti',

        aspectRatios: ['21:9', '16:9', '4:3', '1:1', '3:4', '9:16'],

        durations: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],

        defaultAspectRatio: '16:9',

        defaultDuration: 10,

        resolutionNames: [],

        defaultResolution: ''

    },

    'Seedance 2.0': {

        label: 'Seedance 2.0',

        provider: 'moti',

        aspectRatios: ['21:9', '16:9', '4:3', '1:1', '3:4', '9:16'],

        durations: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],

        defaultAspectRatio: '16:9',

        defaultDuration: 10,

        resolutionNames: [],

        defaultResolution: ''

    }

};



const STORYBOARD_VIDEO_PRICE_MAP = {

    'sora-2': {

        10: 1,

        15: 2,

        25: 3

    },

    'grok': {

        10: 0.49,

        20: 0.98,

        30: 1.47

    },

    'Seedance 2.0 Fast VIP': {},

    'Seedance 2.0 Fast': {},

    'Seedance 2.0 VIP': {},

    'Seedance 2.0': {}

};



function normalizeStoryboardVideoModel(model, defaultModel = DEFAULT_STORYBOARD_VIDEO_MODEL) {

    const normalized = String(model || '').trim();

    if (STORYBOARD_VIDEO_MODEL_CONFIG[normalized]) {

        return normalized;

    }

    const fallback = String(defaultModel || '').trim();

    if (STORYBOARD_VIDEO_MODEL_CONFIG[fallback]) {

        return fallback;

    }

    return DEFAULT_STORYBOARD_VIDEO_MODEL;

}



function getStoryboardVideoModelOptionsHtml(selectedModel = DEFAULT_STORYBOARD_VIDEO_MODEL) {

    const normalizedSelected = normalizeStoryboardVideoModel(selectedModel, DEFAULT_STORYBOARD_VIDEO_MODEL);

    return STORYBOARD_VIDEO_MODEL_OPTIONS.map(item => {

        const selected = item.value === normalizedSelected ? ' selected' : '';

        return `<option value="${item.value}"${selected}>${item.label}</option>`;

    }).join('');

}



function getStoryboardVideoModelConfig(model) {

    const normalized = normalizeStoryboardVideoModel(model, DEFAULT_STORYBOARD_VIDEO_MODEL);

    return STORYBOARD_VIDEO_MODEL_CONFIG[normalized];

}



function normalizeStoryboardVideoAspectRatio(aspectRatio, model, defaultRatio = null) {

    const config = getStoryboardVideoModelConfig(model);

    const legacyMap = {

        '1:2': '9:16',

        '2:1': '16:9'

    };

    const normalized = legacyMap[String(aspectRatio || '').trim()] || String(aspectRatio || '').trim();

    if (config.aspectRatios.includes(normalized)) {

        return normalized;

    }

    const fallback = legacyMap[String(defaultRatio || '').trim()] || String(defaultRatio || '').trim();

    if (config.aspectRatios.includes(fallback)) {

        return fallback;

    }

    return config.defaultAspectRatio;

}



function normalizeStoryboardVideoDuration(duration, model, defaultDuration = null) {

    const config = getStoryboardVideoModelConfig(model);

    const parsed = Number(duration);

    if (config.durations.includes(parsed)) {

        return parsed;

    }

    const fallback = Number(defaultDuration);

    if (config.durations.includes(fallback)) {

        return fallback;

    }

    return config.defaultDuration;

}



function normalizeStoryboardVideoResolutionName(resolutionName, model, defaultResolution = null) {

    const config = getStoryboardVideoModelConfig(model);

    const allowed = Array.isArray(config.resolutionNames) ? config.resolutionNames : [];

    if (!allowed.length) {

        return '';

    }

    const normalized = String(resolutionName || '').trim().toLowerCase();

    const matched = allowed.find(item => String(item).trim().toLowerCase() === normalized);

    if (matched) {

        return matched;

    }

    const fallback = String(defaultResolution ?? config.defaultResolution ?? '').trim().toLowerCase();

    const matchedFallback = allowed.find(item => String(item).trim().toLowerCase() === fallback);

    if (matchedFallback) {

        return matchedFallback;

    }

    return allowed[0] || '';

}



function getStoryboardVideoPrice(model, duration, aspectRatio = '16:9') {

    const normalizedModel = normalizeStoryboardVideoModel(model, DEFAULT_STORYBOARD_VIDEO_MODEL);

    const normalizedDuration = normalizeStoryboardVideoDuration(duration, normalizedModel);

    const normalizedAspectRatio = normalizeStoryboardVideoAspectRatio(aspectRatio, normalizedModel);



    // Try to get price from API data

    const modelPricing = APP_STATE.videoModelPricing[normalizedModel];

    if (modelPricing) {

        // 1. Exact match: duration + aspect_ratio

        const exactKey = `${normalizedDuration}_${normalizedAspectRatio}`;

        if (modelPricing[exactKey]) {

            return modelPricing[exactKey].price_yuan;

        }



        // 2. Fallback: match by duration with any aspect_ratio

        //    (most models have the same price regardless of aspect_ratio)

        for (const key in modelPricing) {

            if (key.startsWith(`${normalizedDuration}_`)) {

                return modelPricing[key].price_yuan;

            }

        }

    }



    // Fall back to old STORYBOARD_VIDEO_PRICE_MAP if not found

    const modelPriceMap = STORYBOARD_VIDEO_PRICE_MAP[normalizedModel] || {};

    return modelPriceMap[normalizedDuration] ?? null;

}



function formatStoryboardVideoPrice(price) {

    if (price === null || price === undefined || Number.isNaN(Number(price))) {

        return '--';

    }

    return Number(price).toFixed(2).replace(/\.00$/, '');

}



function getEpisodeStoryboardVideoSettings() {

    const rawModel = APP_STATE.currentEpisodeInfo?.storyboard_video_model;

    const model = normalizeStoryboardVideoModel(rawModel, DEFAULT_STORYBOARD_VIDEO_MODEL);

    const rawAspectRatio = APP_STATE.currentEpisodeInfo?.storyboard_video_aspect_ratio;

    const aspectRatio = normalizeStoryboardVideoAspectRatio(rawAspectRatio, model);

    const rawDuration = APP_STATE.currentEpisodeInfo?.storyboard_video_duration;

    const duration = normalizeStoryboardVideoDuration(rawDuration, model);

    const rawResolutionName = APP_STATE.currentEpisodeInfo?.storyboard_video_resolution_name;

    const resolutionName = normalizeStoryboardVideoResolutionName(rawResolutionName, model);

    const provider = getStoryboardVideoModelConfig(model).provider;

    const appointAccount = getEpisodeStoryboardVideoAppointAccount();

    return {

        model,

        aspect_ratio: aspectRatio,

        duration,

        resolution_name: resolutionName,

        provider,

        appoint_account: appointAccount

    };

}


function isShotDurationOverrideEnabled(shot) {

    return Boolean(shot && shot.duration_override_enabled);

}


function isShotStoryboardVideoModelOverrideEnabled(shot) {

    return Boolean(shot && shot.storyboard_video_model_override_enabled);

}


function getEffectiveShotStoryboardVideoSettings(shot) {

    const episodeSettings = getEpisodeStoryboardVideoSettings();
    const modelOverrideEnabled = isShotStoryboardVideoModelOverrideEnabled(shot);
    const model = modelOverrideEnabled
        ? normalizeStoryboardVideoModel(shot?.storyboard_video_model, episodeSettings.model)
        : episodeSettings.model;
    const aspectRatio = normalizeStoryboardVideoAspectRatio(
        episodeSettings.aspect_ratio,
        model,
        episodeSettings.aspect_ratio
    );
    const resolutionName = normalizeStoryboardVideoResolutionName(
        episodeSettings.resolution_name,
        model,
        episodeSettings.resolution_name
    );
    const durationOverrideEnabled = isShotDurationOverrideEnabled(shot);
    const defaultDuration = normalizeStoryboardVideoDuration(
        episodeSettings.duration,
        model,
        episodeSettings.duration
    );

    if (!shot || !durationOverrideEnabled) {

        return {

            model,

            aspect_ratio: aspectRatio,

            duration: defaultDuration,

            resolution_name: resolutionName,

            provider: getStoryboardVideoModelConfig(model).provider,

            model_override_enabled: modelOverrideEnabled,

            duration_override_enabled: false,

            appoint_account: getShotStoryboardVideoAppointAccount(shot) || episodeSettings.appoint_account

        };

    }

    const duration = normalizeStoryboardVideoDuration(

        shot.duration,

        model,

        episodeSettings.duration

    );

    return {

        model,

        aspect_ratio: aspectRatio,

        duration,

        resolution_name: resolutionName,

        provider: getStoryboardVideoModelConfig(model).provider,

        model_override_enabled: modelOverrideEnabled,

        duration_override_enabled: true,

        appoint_account: getShotStoryboardVideoAppointAccount(shot) || episodeSettings.appoint_account

    };

}


function syncEpisodeStoryboardVideoSettingsToShotState() {

    APP_STATE.shots = (APP_STATE.shots || []).map(shot => {

        const effectiveSettings = getEffectiveShotStoryboardVideoSettings(shot);

        return {

            ...shot,

            storyboard_video_model: effectiveSettings.model,

            storyboard_video_model_override_enabled: Boolean(effectiveSettings.model_override_enabled),

            aspect_ratio: effectiveSettings.aspect_ratio,

            provider: effectiveSettings.provider,

            duration: effectiveSettings.duration,

            duration_override_enabled: Boolean(effectiveSettings.duration_override_enabled)

        };

    });

    if (APP_STATE.currentShot) {

        const matchedShot = APP_STATE.shots.find(shot => shot.id === APP_STATE.currentShot.id);

        if (matchedShot) {

            APP_STATE.currentShot = matchedShot;

        }

    }

}


function refreshShotDurationControls() {

    if (!APP_STATE.currentShot) return;

    const episodeVideoSettings = getEpisodeStoryboardVideoSettings();
    const effectiveVideoSettings = getEffectiveShotStoryboardVideoSettings(APP_STATE.currentShot);
    const isModelOverrideEnabled = Boolean(effectiveVideoSettings.model_override_enabled);
    const isDurationOverrideEnabled = Boolean(effectiveVideoSettings.duration_override_enabled);
    const shotVideoAccount = getShotStoryboardVideoAppointAccount(APP_STATE.currentShot);
    const isShotAccountSelectable = effectiveVideoSettings.provider === 'moti';
    const globalVideoAccount = getEpisodeStoryboardVideoAppointAccount();

    const modelCheckbox = document.getElementById('shotModelOverrideCheckbox');
    if (modelCheckbox) {

        modelCheckbox.checked = isModelOverrideEnabled;

    }

    const modelSelect = document.getElementById('shotModelSelect');
    if (modelSelect) {

        modelSelect.innerHTML = getStoryboardVideoModelOptionsHtml(effectiveVideoSettings.model);
        modelSelect.disabled = !isModelOverrideEnabled;

    }

    const modelHint = document.getElementById('shotModelHint');
    if (modelHint) {

        modelHint.textContent = isModelOverrideEnabled
            ? `当前镜头单独使用 ${effectiveVideoSettings.model}`
            : `当前跟随图/视频设置默认 ${episodeVideoSettings.model}`;

    }

    const durationCheckbox = document.getElementById('shotDurationOverrideCheckbox');
    if (durationCheckbox) {

        durationCheckbox.checked = isDurationOverrideEnabled;

    }

    const durationSelect = document.getElementById('shotDurationSelect');
    if (durationSelect) {

        durationSelect.innerHTML = getStoryboardVideoModelConfig(effectiveVideoSettings.model).durations.map(item => {

            const selected = item === effectiveVideoSettings.duration ? ' selected' : '';

            return `<option value="${item}"${selected}>${item}秒</option>`;

        }).join('');
        durationSelect.value = String(effectiveVideoSettings.duration);
        durationSelect.disabled = !isDurationOverrideEnabled;

    }

    const durationHint = document.getElementById('shotDurationHint');
    if (durationHint) {

        durationHint.textContent = isDurationOverrideEnabled
            ? `当前镜头单独使用 ${effectiveVideoSettings.duration}s`
            : `当前跟随图/视频设置默认 ${effectiveVideoSettings.duration}s`;

    }

    const appointAccountSelect = document.getElementById('shotVideoAppointAccountSelect');
    if (appointAccountSelect) {

        appointAccountSelect.innerHTML = buildMotiVideoAccountOptionsHtml(shotVideoAccount, {
            blankLabel: `跟随全局账号（${globalVideoAccount || '不指定账号'}）`,
        });
        appointAccountSelect.disabled = !isShotAccountSelectable;

    }

    const appointAccountHint = document.getElementById('shotVideoAppointAccountHint');
    if (appointAccountHint) {

        appointAccountHint.textContent = isShotAccountSelectable
            ? (shotVideoAccount
                ? `当前镜头单独使用账号 ${shotVideoAccount}`
                : `当前跟随全局账号 ${globalVideoAccount || '不指定账号'}`)
            : '当前服务商无需指定账号';

    }

}



function refreshStoryboardVideoSettingButtonLabels() {

    document.querySelectorAll('[data-storyboard-video-setting-btn="1"]').forEach(btn => {

        btn.textContent = '图/视频设置';

    });

}



function updateStoryboardVideoSettingModalFields(forceReset = false) {

    const modelSelect = document.getElementById('storyboardVideoModelSelect');

    const aspectRatioSelect = document.getElementById('storyboardVideoAspectRatioSelect');

    const durationSelect = document.getElementById('storyboardVideoDurationSelect');

    const resolutionSelect = document.getElementById('storyboardVideoResolutionSelect');

    const resolutionGroup = document.getElementById('storyboardVideoResolutionGroup');

    const providerText = document.getElementById('storyboardVideoProviderText');

    const priceText = document.getElementById('storyboardVideoPriceText');

    if (!modelSelect || !aspectRatioSelect || !durationSelect) {

        return;

    }



    const selectedModel = normalizeStoryboardVideoModel(modelSelect.value, DEFAULT_STORYBOARD_VIDEO_MODEL);

    const config = getStoryboardVideoModelConfig(selectedModel);

    const currentSettings = getEpisodeStoryboardVideoSettings();

    const selectedAspectRatio = normalizeStoryboardVideoAspectRatio(

        aspectRatioSelect.value,

        selectedModel,

        forceReset ? config.defaultAspectRatio : currentSettings.aspect_ratio

    );

    const selectedDuration = normalizeStoryboardVideoDuration(

        durationSelect.value,

        selectedModel,

        forceReset ? config.defaultDuration : currentSettings.duration

    );

    const selectedResolution = normalizeStoryboardVideoResolutionName(

        resolutionSelect ? resolutionSelect.value : '',

        selectedModel,

        forceReset ? config.defaultResolution : currentSettings.resolution_name

    );



    aspectRatioSelect.innerHTML = config.aspectRatios.map(ratio => {

        const selected = ratio === selectedAspectRatio ? ' selected' : '';

        return `<option value="${ratio}"${selected}>${ratio}</option>`;

    }).join('');



    durationSelect.innerHTML = config.durations.map(item => {

        const selected = item === selectedDuration ? ' selected' : '';

        return `<option value="${item}"${selected}>${item}秒</option>`;

    }).join('');



    if (resolutionSelect && resolutionGroup) {

        const hasResolutionOptions = Array.isArray(config.resolutionNames) && config.resolutionNames.length > 0;

        resolutionGroup.style.display = hasResolutionOptions ? '' : 'none';

        if (hasResolutionOptions) {

            resolutionSelect.innerHTML = config.resolutionNames.map(item => {

                const selected = item === selectedResolution ? ' selected' : '';

                return `<option value="${item}"${selected}>${item}</option>`;

            }).join('');

        } else {

            resolutionSelect.innerHTML = '';

        }

    }



    const resolvedDuration = Number(durationSelect.value);

    const price = getStoryboardVideoPrice(selectedModel, resolvedDuration);

    if (providerText) {

        const config = getStoryboardVideoModelConfig(selectedModel);

        providerText.textContent = (config && config.provider) ? config.provider : 'yijia';

    }

    if (priceText) {

        priceText.textContent = `¥${formatStoryboardVideoPrice(price)}`;

    }

}



function getDetailImagesRouteHintText(provider, model) {

    const routeOptions = getImageRouteOptions(IMAGE_MODEL_CATALOG, provider, model);

    const ratios = routeOptions.sizes.length ? routeOptions.sizes.join(' / ') : '按上游默认';

    const resolutions = routeOptions.resolutions.length ? routeOptions.resolutions.join(' / ') : '按上游默认';

    return `支持比例：${ratios}；分辨率：${resolutions}`;

}



function updateDetailImagesRouteHint() {

    const providerSelect = document.getElementById('detailImagesProviderSelect');

    const modelSelect = document.getElementById('detailImagesModelSelect');

    const hint = document.getElementById('detailImagesRouteHint');

    if (!providerSelect || !modelSelect || !hint) {

        return;

    }

    hint.textContent = getDetailImagesRouteHintText(providerSelect.value, modelSelect.value);

}



function updateDetailImagesProviderModels() {

    const providerSelect = document.getElementById('detailImagesProviderSelect');

    const modelSelect = document.getElementById('detailImagesModelSelect');

    if (!providerSelect || !modelSelect) {

        return;

    }

    const selection = getDefaultImageSelection(IMAGE_MODEL_CATALOG, providerSelect.value, modelSelect.value);

    providerSelect.value = selection.provider || '';

    modelSelect.innerHTML = buildImageSelectOptions(

        getImageModelsForProvider(IMAGE_MODEL_CATALOG, selection.provider),

        selection.model,

        '选择模型'

    );

    modelSelect.value = selection.model || '';

    updateDetailImagesRouteHint();

}



function openStoryboardVideoSettingModal() {

    if (!APP_STATE.currentEpisode) {

        showToast('请先选择片段', 'warning');

        return;

    }



    closeStoryboardVideoSettingModal();

    ensureImageModelCatalogLoaded().catch(error => {

        console.error('Failed to load image model catalog for storyboard settings:', error);

        showToast('作图模型列表加载失败，将使用当前缓存', 'warning');

    })
        .then(() => {

    const currentSettings = getEpisodeStoryboardVideoSettings();

    const currentDetailImagesProvider = getEpisodeDetailImagesProvider();

    const currentDetailImagesModel = getEpisodeDetailImagesModel();

    const detailImageSelection = getDefaultImageSelection(

        IMAGE_MODEL_CATALOG,

        currentDetailImagesProvider || null,

        currentDetailImagesModel

    );

    const currentIncludeSceneRefs = getEpisodeStoryboard2IncludeSceneReferences();

    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'storyboardVideoSettingModal';

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="closeStoryboardVideoSettingModal()"></div>

        <div class="modal-content shot-image-size-modal">

            <div class="modal-header">

                <h3>图/视频设置</h3>

                <button class="modal-close" onclick="closeStoryboardVideoSettingModal()">&times;</button>

            </div>

            <div class="modal-body shot-image-size-modal-body">

                <div class="form-group shot-image-size-group">

                    <label class="form-label">镜头图服务商</label>

                    <select id="detailImagesProviderSelect" class="form-input shot-image-size-select" onchange="updateDetailImagesProviderModels()">

                        ${buildImageSelectOptions(getImageProviders(IMAGE_MODEL_CATALOG), detailImageSelection.provider, '选择服务商')}

                    </select>

                </div>

                <div class="form-group shot-image-size-group">

                    <label class="form-label">镜头图模型</label>

                    <select id="detailImagesModelSelect" class="form-input shot-image-size-select" onchange="updateDetailImagesRouteHint()">

                        ${buildImageSelectOptions(

                            getImageModelsForProvider(IMAGE_MODEL_CATALOG, detailImageSelection.provider),

                            detailImageSelection.model,

                            '选择模型'

                        )}

                    </select>

                    <div id="detailImagesRouteHint" style="font-size: 12px; color: #8d8d8d; margin-top: 6px;">

                        ${escapeHtml(getDetailImagesRouteHintText(detailImageSelection.provider, detailImageSelection.model))}

                    </div>

                </div>

                <div class="form-group shot-image-size-group">

                    <label class="form-label" style="margin-bottom: 8px;">故事板2镜头图参考图</label>

                    <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">

                        <input id="detailImagesIncludeSceneRefsCheckbox"

                               type="checkbox"

                               ${currentIncludeSceneRefs ? 'checked' : ''}

                               style="width: 16px; height: 16px;" />

                        <span style="font-size: 13px; color: #ddd;">是否携带场景</span>

                    </label>

                    <div style="font-size: 12px; color: #8d8d8d; margin-top: 6px;">

                        仅影响故事板2镜头图；故事板(sora)镜头图会固定携带当前镜头全部主体参考图（角色 / 场景 / 道具）。

                    </div>

                </div>

                <div class="form-group shot-image-size-group">

                    <label class="form-label">视频风格模板</label>

                    <select id="storyboardVideoStyleTemplateSelect" class="form-input shot-image-size-select">

                        <option value="">加载中...</option>

                    </select>

                </div>

                <div class="form-group shot-image-size-group">

                    <label class="form-label">模型</label>

                    <select id="storyboardVideoModelSelect" class="form-input shot-image-size-select" onchange="updateStoryboardVideoSettingModalFields(true)">

                        ${getStoryboardVideoModelOptionsHtml(currentSettings.model)}

                    </select>

                </div>

                <div class="form-group shot-image-size-group" id="storyboardVideoResolutionGroup" style="display: none;">

                    <label class="form-label">视频分辨率</label>

                    <select id="storyboardVideoResolutionSelect" class="form-input shot-image-size-select"></select>

                </div>

                <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px;">

                    <div class="form-group shot-image-size-group" style="margin-bottom: 0;">

                        <label class="form-label">视频比例</label>

                        <select id="storyboardVideoAspectRatioSelect" class="form-input shot-image-size-select"></select>

                    </div>

                    <div class="form-group shot-image-size-group" style="margin-bottom: 0;">

                        <label class="form-label">视频时长</label>

                        <select id="storyboardVideoDurationSelect" class="form-input shot-image-size-select" onchange="updateStoryboardVideoSettingModalFields(false)"></select>

                    </div>

                </div>

                <div style="display:flex; justify-content: space-between; align-items:center; margin-top: 12px; padding: 10px 12px; border: 1px solid #2a2a2a; border-radius: 8px; background: #0a0a0a;">

                    <div style="font-size: 12px; color: #8d8d8d;">

                        服务商：<span id="storyboardVideoProviderText" style="color:#ddd;"></span>

                    </div>

                    <div style="font-size: 13px; color: #8d8d8d;">

                        预估单价：<span id="storyboardVideoPriceText" style="color:#fff; font-weight: 600;"></span>

                    </div>

                </div>

                <div class="shot-image-size-actions">

                    <button class="secondary-button shot-image-size-btn" onclick="closeStoryboardVideoSettingModal()">取消</button>

                    <button class="primary-button shot-image-size-btn" id="saveStoryboardVideoSettingBtn" onclick="saveStoryboardVideoSettings()">保存设置</button>

                </div>

            </div>

        </div>

    `;

    document.body.appendChild(modal);

    updateDetailImagesProviderModels();

    updateStoryboardVideoSettingModalFields(false);

    loadVideoStyleTemplateOptions();

        });

}



async function loadVideoStyleTemplateOptions() {

    const select = document.getElementById('storyboardVideoStyleTemplateSelect');

    if (!select) return;

    try {

        const response = await apiRequest('/api/video-style-templates');

        const templates = await response.json();

        const currentId = APP_STATE.currentEpisodeInfo?.video_style_template_id;
        const preferredTemplate = templates.find(t => t.is_default)
            || templates[0];
        const selectedTemplateId = currentId || preferredTemplate?.id || null;



        select.innerHTML = templates.map(t => {

            const isSelected = selectedTemplateId ? (t.id === selectedTemplateId) : false;

            return `<option value="${t.id}" ${isSelected ? 'selected' : ''}>${t.name}${t.is_default ? ' (默认)' : ''}</option>`;

        }).join('');



        if (!templates.length) {

            select.innerHTML = '<option value="">暂无模板</option>';

        }

    } catch (error) {

        console.error('Failed to load video style templates:', error);

        select.innerHTML = '<option value="">加载失败</option>';

    }

}



function closeStoryboardVideoSettingModal() {

    const modal = document.getElementById('storyboardVideoSettingModal');

    if (modal) {

        modal.remove();

    }

}



async function saveStoryboardVideoSettings() {

    if (!APP_STATE.currentEpisode) {

        return;

    }



    const detailImagesProviderSelect = document.getElementById('detailImagesProviderSelect');

    const detailImagesModelSelect = document.getElementById('detailImagesModelSelect');

    const detailImagesIncludeSceneRefsCheckbox = document.getElementById('detailImagesIncludeSceneRefsCheckbox');

    const modelSelect = document.getElementById('storyboardVideoModelSelect');

    const aspectRatioSelect = document.getElementById('storyboardVideoAspectRatioSelect');

    const durationSelect = document.getElementById('storyboardVideoDurationSelect');

    const resolutionSelect = document.getElementById('storyboardVideoResolutionSelect');

    const saveBtn = document.getElementById('saveStoryboardVideoSettingBtn');

    if (!detailImagesProviderSelect || !detailImagesModelSelect || !detailImagesIncludeSceneRefsCheckbox || !modelSelect || !aspectRatioSelect || !durationSelect) {

        return;

    }



    const detailImagesProvider = normalizeDetailImagesProvider(detailImagesProviderSelect.value, getEpisodeDetailImagesProvider());

    const detailImagesModel = normalizeDetailImagesModel(detailImagesModelSelect.value, getEpisodeDetailImagesModel());

    if (!detailImagesProvider || !detailImagesModel) {

        showToast('请选择镜头图服务商和模型', 'warning');

        return;

    }

    const detailImagesCw = getEpisodeStoryboard2ImageCw();

    const detailImagesIncludeSceneRefs = Boolean(detailImagesIncludeSceneRefsCheckbox.checked);

    const model = normalizeStoryboardVideoModel(modelSelect.value, DEFAULT_STORYBOARD_VIDEO_MODEL);

    const aspectRatio = normalizeStoryboardVideoAspectRatio(aspectRatioSelect.value, model);

    const duration = normalizeStoryboardVideoDuration(durationSelect.value, model);

    const resolutionName = normalizeStoryboardVideoResolutionName(
        resolutionSelect ? resolutionSelect.value : '',
        model,
        getEpisodeStoryboardVideoSettings().resolution_name
    );



    const styleTemplateSelect = document.getElementById('storyboardVideoStyleTemplateSelect');

    const videoStyleTemplateId = styleTemplateSelect ? parseInt(styleTemplateSelect.value) || 0 : 0;

    if (saveBtn) {

        saveBtn.disabled = true;

        saveBtn.textContent = '保存中...';

    }



    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/storyboard-video-settings`, {

            method: 'PATCH',

            body: JSON.stringify({

                detail_images_provider: detailImagesProvider,

                detail_images_model: detailImagesModel,

                storyboard2_image_cw: detailImagesCw,

                storyboard2_include_scene_references: detailImagesIncludeSceneRefs,

                model,

                aspect_ratio: aspectRatio,

                duration,

                resolution_name: resolutionName,

                video_style_template_id: videoStyleTemplateId

            })

        });



        let result = null;

        try {

            result = await response.json();

        } catch (error) {

            result = null;

        }



        if (!response.ok) {

            throw new Error(result?.detail || '图/视频设置保存失败');

        }



        APP_STATE.currentEpisodeInfo = APP_STATE.currentEpisodeInfo || {};

        APP_STATE.currentEpisodeInfo.detail_images_provider = normalizeDetailImagesProvider(

            result?.detail_images_provider || detailImagesProvider,

            detailImagesProvider

        );

        APP_STATE.currentEpisodeInfo.detail_images_model = normalizeDetailImagesModel(

            result?.detail_images_model || detailImagesModel,

            detailImagesModel

        );

        APP_STATE.currentEpisodeInfo.storyboard2_image_cw = normalizeStoryboard2ImageCw(

            result?.storyboard2_image_cw ?? detailImagesCw,

            detailImagesCw

        );

        APP_STATE.currentEpisodeInfo.storyboard2_include_scene_references = Boolean(

            result?.storyboard2_include_scene_references ?? detailImagesIncludeSceneRefs

        );

        APP_STATE.currentEpisodeInfo.storyboard_video_model = normalizeStoryboardVideoModel(result?.model || model, DEFAULT_STORYBOARD_VIDEO_MODEL);

        APP_STATE.currentEpisodeInfo.storyboard_video_aspect_ratio = normalizeStoryboardVideoAspectRatio(

            result?.aspect_ratio || aspectRatio,

            APP_STATE.currentEpisodeInfo.storyboard_video_model

        );

        APP_STATE.currentEpisodeInfo.storyboard_video_duration = normalizeStoryboardVideoDuration(

            result?.duration || duration,

            APP_STATE.currentEpisodeInfo.storyboard_video_model

        );

        APP_STATE.currentEpisodeInfo.storyboard_video_resolution_name = normalizeStoryboardVideoResolutionName(
            result?.resolution_name || resolutionName,
            APP_STATE.currentEpisodeInfo.storyboard_video_model,
            resolutionName
        );

        APP_STATE.currentEpisodeInfo.video_prompt_template = result?.video_prompt_template
            ?? APP_STATE.currentEpisodeInfo.video_prompt_template
            ?? '';

        APP_STATE.currentEpisodeInfo.shot_image_size = normalizeShotImageSize(

            result?.shot_image_size || APP_STATE.currentEpisodeInfo.storyboard_video_aspect_ratio

        );

        APP_STATE.currentEpisodeInfo.video_style_template_id = result?.video_style_template_id ?? videoStyleTemplateId;

        syncEpisodeStoryboardVideoSettingsToShotState();


        refreshShotImageSizeButtonLabels();

        refreshStoryboardVideoSettingButtonLabels();

        renderStoryboardShotsGrid();

        renderStoryboardSidebar();

        closeStoryboardVideoSettingModal();

        showToast(

            `图/视频设置已保存：${APP_STATE.currentEpisodeInfo.storyboard_video_aspect_ratio} / ${APP_STATE.currentEpisodeInfo.detail_images_provider} / ${getDetailImagesModelLabel(APP_STATE.currentEpisodeInfo.detail_images_model)} / 故事板2${APP_STATE.currentEpisodeInfo.storyboard2_include_scene_references ? '携带场景' : '不携带场景'} / ${APP_STATE.currentEpisodeInfo.storyboard_video_model} / ${APP_STATE.currentEpisodeInfo.storyboard_video_duration}s${APP_STATE.currentEpisodeInfo.storyboard_video_resolution_name ? ` / ${APP_STATE.currentEpisodeInfo.storyboard_video_resolution_name}` : ''}`,

            'success'

        );

    } catch (error) {

        console.error('Failed to save storyboard video settings:', error);

        showToast(`保存失败: ${error.message}`, 'error');

    } finally {

        if (saveBtn) {

            saveBtn.disabled = false;

            saveBtn.textContent = '保存设置';

        }

    }

}



function refreshShotImageSizeButtonLabels() {

    document.querySelectorAll('[data-shot-image-size-btn="1"]').forEach(btn => {

        btn.textContent = '图/视频设置';

    });

}



function openShotImageSizeSettingModal() {

    if (!APP_STATE.currentEpisode) {

        showToast('请先选择片段', 'warning');

        return;

    }



    closeShotImageSizeSettingModal();



    const currentSize = getEpisodeShotImageSize();

    const currentDuration = getEpisodeStoryboard2VideoDuration();

    const currentImageCw = getEpisodeStoryboard2ImageCw();

    const currentIncludeSceneRefs = getEpisodeStoryboard2IncludeSceneReferences();

    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'shotImageSizeSettingModal';

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="closeShotImageSizeSettingModal()"></div>

        <div class="modal-content shot-image-size-modal">

            <div class="modal-header">

                <h3>图/视频设置</h3>

                <button class="modal-close" onclick="closeShotImageSizeSettingModal()">&times;</button>

            </div>

            <div class="modal-body shot-image-size-modal-body">

                <div style="font-size: 12px; color: #8d8d8d; margin-bottom: 12px;">

                    配置按剧集保存，作用于“故事板2”。

                </div>

                <div class="form-group shot-image-size-group">

                    <label class="form-label">镜头图比例</label>

                    <select id="episodeShotImageSizeSelect" class="form-input shot-image-size-select">

                        ${getShotImageSizeOptionsHtml(currentSize)}

                    </select>

                </div>

                <div class="form-group shot-image-size-group">

                    <label class="form-label">故事板2视频时长</label>

                    <select id="episodeStoryboard2VideoDurationSelect" class="form-input shot-image-size-select">

                        ${getStoryboard2VideoDurationOptionsHtml(currentDuration)}

                    </select>

                </div>

                <div class="form-group shot-image-size-group">

                    <label class="form-label">镜头图 CW</label>

                    <input id="episodeStoryboard2ImageCwInput"

                           class="form-input shot-image-size-select"

                           type="number"

                           min="1"

                           max="100"

                           step="1"

                           value="${currentImageCw}" />

                </div>

                <div class="form-group shot-image-size-group">

                    <label class="form-label" style="margin-bottom: 8px;">镜头图参考图</label>

                    <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">

                        <input id="episodeStoryboard2IncludeSceneRefsCheckbox"

                               type="checkbox"

                               ${currentIncludeSceneRefs ? 'checked' : ''}

                               style="width: 16px; height: 16px;" />

                        <span style="font-size: 13px; color: #ddd;">是否携带场景</span>

                    </label>

                    <div style="font-size: 12px; color: #8d8d8d; margin-top: 6px;">

                        不勾选时，仅携带角色主体参考图。

                    </div>

                </div>

                <div class="shot-image-size-actions">

                    <button class="secondary-button shot-image-size-btn" onclick="closeShotImageSizeSettingModal()">取消</button>

                    <button class="primary-button shot-image-size-btn" id="saveEpisodeShotImageSizeBtn" onclick="saveEpisodeShotImageSize()">保存设置</button>

                </div>

            </div>

        </div>

    `;



    document.body.appendChild(modal);

}



function closeShotImageSizeSettingModal() {

    const modal = document.getElementById('shotImageSizeSettingModal');

    if (modal) {

        modal.remove();

    }

}



async function saveEpisodeShotImageSize() {

    if (!APP_STATE.currentEpisode) {

        return;

    }



    const select = document.getElementById('episodeShotImageSizeSelect');

    const durationSelect = document.getElementById('episodeStoryboard2VideoDurationSelect');

    const imageCwInput = document.getElementById('episodeStoryboard2ImageCwInput');

    const includeSceneRefsCheckbox = document.getElementById('episodeStoryboard2IncludeSceneRefsCheckbox');

    const saveBtn = document.getElementById('saveEpisodeShotImageSizeBtn');

    const selectedSize = normalizeShotImageSize(select?.value || '9:16');

    const selectedDuration = normalizeStoryboard2VideoDuration(durationSelect?.value || 6);

    const selectedImageCw = normalizeStoryboard2ImageCw(imageCwInput?.value, getEpisodeStoryboard2ImageCw());

    const selectedIncludeSceneRefs = Boolean(includeSceneRefsCheckbox?.checked);



    if (saveBtn) {

        saveBtn.disabled = true;

        saveBtn.textContent = '保存中...';

    }



    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/shot-image-size`, {

            method: 'PATCH',

            body: JSON.stringify({

                shot_image_size: selectedSize,

                storyboard2_video_duration: selectedDuration,

                storyboard2_image_cw: selectedImageCw,

                storyboard2_include_scene_references: selectedIncludeSceneRefs

            })

        });



        let result = null;

        try {

            result = await response.json();

        } catch (error) {

            result = null;

        }



        if (!response.ok) {

            throw new Error(result?.detail || '尺寸设置保存失败');

        }



        APP_STATE.currentEpisodeInfo = APP_STATE.currentEpisodeInfo || {};

        APP_STATE.currentEpisodeInfo.shot_image_size = normalizeShotImageSize(result?.shot_image_size || selectedSize);

        APP_STATE.currentEpisodeInfo.storyboard2_video_duration = normalizeStoryboard2VideoDuration(

            result?.storyboard2_video_duration || selectedDuration

        );

        APP_STATE.currentEpisodeInfo.storyboard2_image_cw = normalizeStoryboard2ImageCw(

            result?.storyboard2_image_cw ?? selectedImageCw,

            selectedImageCw

        );

        APP_STATE.currentEpisodeInfo.storyboard2_include_scene_references = Boolean(

            result?.storyboard2_include_scene_references ?? selectedIncludeSceneRefs

        );

        refreshShotImageSizeButtonLabels();

        refreshStoryboardVideoSettingButtonLabels();

        closeShotImageSizeSettingModal();

        showToast(

            `图/视频设置已保存：${APP_STATE.currentEpisodeInfo.shot_image_size} / ${APP_STATE.currentEpisodeInfo.storyboard2_video_duration}s / ${APP_STATE.currentEpisodeInfo.storyboard2_include_scene_references ? '携带场景' : '不携带场景'}`,

            'success'

        );

    } catch (error) {

        console.error('Failed to save episode shot image size:', error);

        showToast(`保存失败: ${error.message}`, 'error');

    } finally {

        if (saveBtn) {

            saveBtn.disabled = false;

            saveBtn.textContent = '保存设置';

        }

    }

}



async function runTasksWithConcurrencyLimit(tasks, worker, limit = 3) {

    const queue = Array.isArray(tasks) ? tasks : [];

    if (queue.length === 0) {

        return;

    }



    const workerCount = Math.max(1, Math.min(limit, queue.length));

    let cursor = 0;

    const runners = Array.from({ length: workerCount }, async () => {

        while (cursor < queue.length) {

            const taskIndex = cursor;

            cursor += 1;

            await worker(queue[taskIndex], taskIndex);

        }

    });

    await Promise.all(runners);

}



let batchGenerateImageModalState = null;



function openBatchGenerateImageModal(config) {

    closeBatchGenerateImageModal();



    const targets = Array.isArray(config?.targets) ? config.targets : [];

    if (targets.length === 0) {

        showToast('没有可生成的镜头', 'info');

        return;

    }



    batchGenerateImageModalState = {

        type: config?.type || '',

        confirmHandler: config?.confirmHandler || null

    };



    const ratio = config?.type === 'storyboard1'
        ? getStoryboardSoraShotImageSize()
        : getEpisodeShotImageSize();

    const videoDuration = getEpisodeStoryboard2VideoDuration();

    const rowsHtml = targets.map(target => `

        <label style="display:flex; align-items:flex-start; gap:8px; padding:6px; cursor:pointer; border-radius:2px; transition:background 0.2s;"

               onmouseover="this.style.background='#1a1a1a'"

               onmouseout="this.style.background='transparent'">

            <input type="checkbox"

                   class="batch-image-target-checkbox"

                   data-target-id="${target.id}"

                   checked

                   style="width:16px; height:16px; margin-top:2px; cursor:pointer;">

            <div style="display:flex; flex-direction:column; gap:2px;">

                <span style="color:#fff; font-size:13px; line-height:1.4;">${escapeHtml(target.label || '')}</span>

                ${target.desc ? `<span style="color:#7a7a7a; font-size:11px; line-height:1.4;">${escapeHtml(target.desc)}</span>` : ''}

            </div>

        </label>

    `).join('');



    const modal = document.createElement('div');

    modal.className = 'modal form-modal active';

    modal.id = 'batchGenerateImageModal';

    modal.innerHTML = `

        <div class="modal-content" style="max-width: 640px;">

            <div class="modal-header">

                <h3>${escapeHtml(config?.title || '批量生成镜头图')}</h3>

                <button class="modal-close" onclick="closeBatchGenerateImageModal()">&times;</button>

            </div>

            <div class="modal-body">

                <div style="font-size: 12px; color: #8d8d8d; margin-bottom: 10px;">

                    当前图/视频设置：<span style="color:#fff;">${escapeHtml(ratio)} / ${videoDuration}s</span>

                    <span style="color:#666;">（可在“图/视频设置”中修改）</span>

                </div>

                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">

                    <span class="form-label" style="margin: 0;">选择镜头</span>

                    <div style="display: flex; gap: 8px;">

                        <button class="secondary-button" style="padding: 4px 10px; font-size: 12px;" onclick="selectAllBatchImageTargets()">全选</button>

                        <button class="secondary-button" style="padding: 4px 10px; font-size: 12px;" onclick="unselectAllBatchImageTargets()">取消全选</button>

                    </div>

                </div>

                <div id="batchImageTargetsList" style="max-height: 280px; overflow-y: auto; border: 1px solid #2a2a2a; border-radius: 4px; padding: 10px; background: #0a0a0a;">

                    ${rowsHtml}

                </div>

                <div class="modal-form-actions">

                    <button class="secondary-button" onclick="closeBatchGenerateImageModal()">取消</button>

                    <button class="primary-button" id="batchGenerateImageConfirmBtn" onclick="confirmBatchGenerateImageModal()">开始生成</button>

                </div>

            </div>

        </div>

    `;



    const clickHandler = (event) => {

        if (event.target === modal) {

            closeBatchGenerateImageModal();

            modal.removeEventListener('click', clickHandler);

        }

    };

    modal.addEventListener('click', clickHandler);

    document.body.appendChild(modal);

}



function closeBatchGenerateImageModal() {

    const modal = document.getElementById('batchGenerateImageModal');

    if (modal) {

        modal.remove();

    }

    batchGenerateImageModalState = null;

}



function selectAllBatchImageTargets() {

    document.querySelectorAll('.batch-image-target-checkbox').forEach(cb => {

        cb.checked = true;

    });

}



function unselectAllBatchImageTargets() {

    document.querySelectorAll('.batch-image-target-checkbox').forEach(cb => {

        cb.checked = false;

    });

}



async function confirmBatchGenerateImageModal() {

    if (!batchGenerateImageModalState || typeof batchGenerateImageModalState.confirmHandler !== 'function') {

        return;

    }



    const selectedIds = Array.from(document.querySelectorAll('.batch-image-target-checkbox:checked'))

        .map(cb => Number(cb.dataset.targetId))

        .filter(id => Number.isInteger(id) && id > 0);



    if (selectedIds.length === 0) {

        showToast('请至少选择一个镜头', 'warning');

        return;

    }



    const confirmHandler = batchGenerateImageModalState.confirmHandler;

    closeBatchGenerateImageModal();

    try {

        await confirmHandler(selectedIds);

    } catch (error) {

        console.error('Failed to confirm batch image generation:', error);

        showToast(`批量生成失败: ${error.message || '未知错误'}`, 'error');

    }

}



async function batchGenerateStoryboardImageShots() {

    const mainShots = (APP_STATE.shots || []).filter(shot => Number(shot.variant_index || 0) === 0);

    if (mainShots.length === 0) {

        showToast('没有可生成的镜头', 'info');

        return;

    }



    openBatchGenerateImageModal({

        type: 'storyboard1',

        title: '批量生成镜头图',

        targets: mainShots.map(shot => ({

            id: shot.id,

            label: `镜头 ${shot.shot_number}`

        })),

        confirmHandler: async (selectedShotIds) => {

            const selectedSize = getStoryboardSoraShotImageSize();

            await ensureImageModelCatalogLoaded();

            const selectedModel = getEpisodeDetailImagesModel();

            const imageSelection = getDefaultImageSelection(

                IMAGE_MODEL_CATALOG,

                getEpisodeDetailImagesProvider() || null,

                selectedModel

            );

            showToast(`正在提交 ${selectedShotIds.length} 个镜头任务...`, 'info');



            let successCount = 0;

            let failedCount = 0;



            await runTasksWithConcurrencyLimit(selectedShotIds, async (shotId) => {

                try {

                    const response = await apiRequest(`/api/shots/${shotId}/generate-detail-images`, {

                        method: 'POST',

                        body: JSON.stringify({

                            size: selectedSize,

                            provider: imageSelection.provider,

                            model: imageSelection.model || selectedModel,

                            resolution: imageSelection.resolution

                        })

                    });



                    let result = null;

                    try {

                        result = await response.json();

                    } catch (error) {

                        result = null;

                    }



                    if (!response.ok) {

                        throw new Error(result?.detail || '提交失败');

                    }



                    APP_STATE.previousProcessingTasks.add(`${shotId}:detail_images`);

                    successCount += 1;

                } catch (error) {

                    failedCount += 1;

                    console.error(`Failed to batch generate detail images for shot ${shotId}:`, error);

                }

            }, 3);



            try {

                const shotsResponse = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/shots`);

                if (shotsResponse.ok) {

                    APP_STATE.shots = await shotsResponse.json();

                    if (APP_STATE.currentShot) {

                        const updatedCurrent = APP_STATE.shots.find(shot => shot.id === APP_STATE.currentShot.id);

                        if (updatedCurrent) {

                            APP_STATE.currentShot = updatedCurrent;

                        }

                    }

                    renderStoryboardShotsGrid();

                    renderStoryboardSidebar();

                }

            } catch (error) {

                console.error('Failed to refresh shots after batch detail image generation:', error);

            }



            if (successCount > 0) {

                startVideoStatusPolling();

            }



            if (failedCount === 0) {

                showToast(`已提交 ${successCount} 个镜头图生成任务`, 'success');

            } else {

                showToast(`提交完成：成功 ${successCount}，失败 ${failedCount}`, 'warning');

            }

        }

    });

}



async function batchGenerateStoryboard2ImageShots() {

    const state = ensureStoryboard2EpisodeState();

    const shots = state?.boardData?.shots || [];

    if (shots.length === 0) {

        showToast('没有可生成的镜头', 'info');

        return;

    }



    openBatchGenerateImageModal({

        type: 'storyboard2',

        title: '批量生成镜头图',

        targets: shots.map(shot => ({

            id: Number(shot.id),

            label: `镜头 ${shot.shot_label || shot.shot_number || shot.id || ''}`

        })),

        confirmHandler: async (selectedShotIds) => {

            const currentState = ensureStoryboard2EpisodeState();

            const boardShots = currentState?.boardData?.shots || [];

            const selectedSet = new Set(selectedShotIds.map(id => Number(id)));

            const selectedSubShots = [];



            boardShots.forEach(shot => {

                if (!selectedSet.has(Number(shot.id))) {

                    return;

                }

                (shot.sub_shots || []).forEach(row => {

                    if (row?.id) {

                        selectedSubShots.push({

                            id: Number(row.id)

                        });

                    }

                });

            });



            if (selectedSubShots.length === 0) {

                showToast('所选镜头没有可生成的分镜', 'warning');

                return;

            }



            const selectedSize = getEpisodeShotImageSize();

            await ensureImageModelCatalogLoaded();

            const imageSelection = getDefaultImageSelection(

                IMAGE_MODEL_CATALOG,

                getEpisodeDetailImagesProvider() || null,

                getEpisodeDetailImagesModel()

            );

            APP_STATE.storyboard2GeneratingBySubShot = APP_STATE.storyboard2GeneratingBySubShot || {};

            selectedSubShots.forEach(item => {

                APP_STATE.storyboard2GeneratingBySubShot[item.id] = true;

            });

            renderStoryboard2Layout();



            showToast(`正在提交 ${selectedSubShots.length} 个分镜任务...`, 'info');



            let successCount = 0;

            let failedCount = 0;

            try {

                await runTasksWithConcurrencyLimit(selectedSubShots, async (item) => {

                    try {

                        const response = await apiRequest(`/api/storyboard2/subshots/${item.id}/generate-images`, {

                            method: 'POST',

                            body: JSON.stringify({

                                provider: imageSelection.provider,

                                model: imageSelection.model,

                                size: selectedSize,

                                resolution: imageSelection.resolution

                            })

                        });



                        let result = null;

                        try {

                            result = await response.json();

                        } catch (error) {

                            result = null;

                        }



                        if (!response.ok) {

                            throw new Error(result?.detail || '提交失败');

                        }



                        successCount += 1;

                    } catch (error) {

                        failedCount += 1;

                        console.error(`Failed to batch generate storyboard2 images for sub_shot ${item.id}:`, error);

                    }

                }, 3);

            } finally {

                selectedSubShots.forEach(item => {

                    delete APP_STATE.storyboard2GeneratingBySubShot[item.id];

                });

            }



            await refreshStoryboard2BoardDataAndRender();

            if (successCount > 0) {

                startStoryboard2GenerationPolling();

            }



            if (failedCount === 0) {

                showToast(`已提交 ${successCount} 个分镜镜头图任务`, 'success');

            } else {

                showToast(`提交完成：成功 ${successCount}，失败 ${failedCount}`, 'warning');

            }

        }

    });

}



async function batchGenerateStoryboard2Videos() {

    const state = ensureStoryboard2EpisodeState();

    const shots = state?.boardData?.shots || [];

    if (shots.length === 0) {

        showToast('没有可生成的镜头', 'info');

        return;

    }



    openBatchGenerateImageModal({

        type: 'storyboard2-video',

        title: '批量生成视频',

        targets: shots.map(shot => ({

            id: Number(shot.id),

            label: `镜头 ${shot.shot_label || shot.shot_number || shot.id || ''}`

        })),

        confirmHandler: async (selectedShotIds) => {

            const currentState = ensureStoryboard2EpisodeState();

            const boardShots = currentState?.boardData?.shots || [];

            const selectedSet = new Set(selectedShotIds.map(id => Number(id)));

            const selectedSubShots = [];



            boardShots.forEach(shot => {

                if (!selectedSet.has(Number(shot.id))) {

                    return;

                }

                (shot.sub_shots || []).forEach(row => {

                    if (row?.id) {

                        selectedSubShots.push({

                            id: Number(row.id)

                        });

                    }

                });

            });



            if (selectedSubShots.length === 0) {

                showToast('所选镜头没有可生成的视频分镜', 'warning');

                return;

            }



            const imageSize = getEpisodeShotImageSize();

            const videoDuration = getEpisodeStoryboard2VideoDuration();



            APP_STATE.storyboard2VideoGeneratingBySubShot = APP_STATE.storyboard2VideoGeneratingBySubShot || {};

            selectedSubShots.forEach(item => {

                APP_STATE.storyboard2VideoGeneratingBySubShot[item.id] = true;

            });

            renderStoryboard2Layout();



            showToast(`正在提交 ${selectedSubShots.length} 个分镜视频任务...`, 'info');



            let successCount = 0;

            let failedCount = 0;

            try {

                await runTasksWithConcurrencyLimit(selectedSubShots, async (item) => {

                    try {

                        const response = await apiRequest(`/api/storyboard2/subshots/${item.id}/generate-video`, {

                            method: 'POST',

                            body: JSON.stringify({

                                aspect_ratio: imageSize,

                                duration: videoDuration,

                                resolution_name: getEpisodeStoryboardVideoSettings().resolution_name

                            })

                        });



                        let result = null;

                        try {

                            result = await response.json();

                        } catch (error) {

                            result = null;

                        }



                        if (!response.ok) {

                            throw new Error(result?.detail || '提交失败');

                        }



                        successCount += 1;

                    } catch (error) {

                        failedCount += 1;

                        console.error(`Failed to batch generate storyboard2 videos for sub_shot ${item.id}:`, error);

                    }

                }, 3);

            } finally {

                selectedSubShots.forEach(item => {

                    delete APP_STATE.storyboard2VideoGeneratingBySubShot[item.id];

                });

            }



            await refreshStoryboard2BoardDataAndRender();

            if (successCount > 0) {

                startStoryboard2GenerationPolling();

            }



            if (failedCount === 0) {

                showToast(`已提交 ${successCount} 个分镜视频任务`, 'success');

            } else {

                showToast(`提交完成：成功 ${successCount}，失败 ${failedCount}`, 'warning');

            }

        }

    });

}



async function batchDownloadStoryboard2Videos() {

    const entries = getStoryboard2BatchDownloadEntries('video');

    if (entries.length === 0) {

        showToast('故事板2暂无可下载视频', 'warning');

        return;

    }

    openStoryboard2BatchDownloadModal({

        type: 'video',

        title: '批量下载视频',

        hint: '可选择需要下载的分镜视频，默认全选。'

    });

}



async function batchDownloadStoryboard2Images() {

    const entries = getStoryboard2BatchDownloadEntries('image');

    if (entries.length === 0) {

        showToast('故事板2暂无可下载图片', 'warning');

        return;

    }

    openStoryboard2BatchDownloadModal({

        type: 'image',

        title: '批量下载图片',

        hint: '优先下载“当前图片区”图片，若为空则下载首张候选图。默认全选。'

    });

}



function normalizeStoryboard2ShotNumberForFile(shot, fallbackIndex) {

    const rawText = String(shot?.shot_label || shot?.shot_number || fallbackIndex || '').trim();

    if (!rawText) {

        return String(fallbackIndex || 1);

    }

    const match = rawText.match(/\d+/);

    if (match && match[0]) {

        return match[0];

    }

    const sanitized = rawText.replace(/[^\w\u4e00-\u9fa5-]/g, '');

    return sanitized || String(fallbackIndex || 1);

}



function getFileExtensionFromUrl(url, fallbackExt = '.bin') {

    const fileName = extractFileNameFromUrl(url);

    const dotIndex = fileName.lastIndexOf('.');

    if (dotIndex >= 0 && dotIndex < fileName.length - 1) {

        return fileName.substring(dotIndex);

    }

    return fallbackExt;

}



function getStoryboard2BatchDownloadEntries(type = 'video') {

    const state = ensureStoryboard2EpisodeState();

    const shots = state?.boardData?.shots || [];

    if (!Array.isArray(shots) || shots.length === 0) {

        return [];

    }



    const entries = [];

    shots.forEach((shot, shotIndex) => {

        const shotNo = normalizeStoryboard2ShotNumberForFile(shot, shotIndex + 1);

        const subShots = Array.isArray(shot?.sub_shots) ? shot.sub_shots : [];

        subShots.forEach((row, rowIndex) => {

            const subIndex = Number.parseInt(row?.order, 10) || (rowIndex + 1);

            let downloadUrl = '';

            let fallbackExt = '.bin';



            if (type === 'video') {

                const videos = Array.isArray(row?.videos) ? row.videos : [];

                const latestCompletedVideo = [...videos].reverse().find(video => {

                    const status = String(video?.status || '').toLowerCase();

                    return status === 'completed' && String(video?.video_url || '').trim();

                });

                if (latestCompletedVideo?.video_url) {

                    downloadUrl = String(latestCompletedVideo.video_url).trim();

                    fallbackExt = '.mp4';

                }

            } else {

                const currentImageUrl = String(row?.current_image?.image_url || '').trim();

                if (currentImageUrl) {

                    downloadUrl = currentImageUrl;

                } else {

                    const candidates = Array.isArray(row?.candidates) ? row.candidates : [];

                    const candidate = candidates.find(item => String(item?.image_url || '').trim());

                    if (candidate?.image_url) {

                        downloadUrl = String(candidate.image_url).trim();

                    }

                }

                fallbackExt = '.png';

            }



            if (!downloadUrl) {

                return;

            }



            const fileExt = getFileExtensionFromUrl(downloadUrl, fallbackExt);

            const fileName = `${shotNo}_${subIndex}${fileExt}`;

            entries.push({

                id: `${type}_${shotNo}_${subIndex}_${entries.length + 1}`,

                shotNo,

                subIndex,

                label: `镜头 ${shotNo}_${subIndex}`,

                fileName,

                downloadUrl

            });

        });

    });



    return entries;

}



function closeStoryboard2BatchDownloadModal() {

    const modal = document.getElementById('storyboard2BatchDownloadModal');

    if (modal) {

        modal.remove();

    }

}



function updateStoryboard2BatchDownloadProgress(modal, current, total) {

    const textEl = modal?.querySelector('[data-sb2-download-progress-text]');

    const barEl = modal?.querySelector('[data-sb2-download-progress-bar]');

    if (textEl) {

        textEl.textContent = `${current}/${total}`;

    }

    if (barEl) {

        const percent = total > 0 ? (current / total) * 100 : 0;

        barEl.style.width = `${Math.max(0, Math.min(100, percent))}%`;

    }

}



async function downloadFileWithCustomName(fileUrl, fileName) {

    const response = await fetch(fileUrl);

    if (!response.ok) {

        throw new Error(`HTTP ${response.status}`);

    }

    const blob = await response.blob();

    const blobUrl = URL.createObjectURL(blob);

    const link = document.createElement('a');

    link.href = blobUrl;

    link.download = fileName;

    document.body.appendChild(link);

    link.click();

    document.body.removeChild(link);

    URL.revokeObjectURL(blobUrl);

}



function openStoryboard2BatchDownloadModal({ type, title, hint }) {

    closeStoryboard2BatchDownloadModal();



    const entries = getStoryboard2BatchDownloadEntries(type);

    if (entries.length === 0) {

        showToast(type === 'video' ? '故事板2暂无可下载视频' : '故事板2暂无可下载图片', 'warning');

        return;

    }



    const modal = document.createElement('div');

    modal.className = 'modal form-modal active';

    modal.id = 'storyboard2BatchDownloadModal';

    modal.innerHTML = `

        <div class="modal-backdrop"></div>

        <div class="modal-content" style="max-width: 620px;">

            <div class="modal-header">

                <h3>${escapeHtml(title || '批量下载')}</h3>

                <button class="modal-close" type="button">&times;</button>

            </div>

            <div class="modal-body">

                <p style="color:#999;font-size:13px;margin-bottom:12px;">${escapeHtml(hint || '')}</p>

                <div class="form-group">

                    <label class="form-label">选择分镜</label>

                    <div style="display:flex;gap:10px;margin-bottom:10px;">

                        <button class="secondary-button" type="button" style="flex:1;" data-sb2-download-select-all>全选</button>

                        <button class="secondary-button" type="button" style="flex:1;" data-sb2-download-unselect-all>取消全选</button>

                    </div>

                    <div style="max-height:260px;overflow-y:auto;border:1px solid #2a2a2a;border-radius:4px;padding:10px;background:#0a0a0a;" data-sb2-download-list>

                        ${entries.map(entry => `

                            <label style="display:flex;align-items:center;gap:8px;padding:6px;cursor:pointer;border-radius:2px;transition:background .2s;"

                                   onmouseover="this.style.background='#1a1a1a'"

                                   onmouseout="this.style.background='transparent'">

                                <input type="checkbox"

                                       class="sb2-batch-download-checkbox"

                                       data-file-url="${escapeHtml(entry.downloadUrl)}"

                                       data-file-name="${escapeHtml(entry.fileName)}"

                                       data-file-label="${escapeHtml(entry.label)}"

                                       checked

                                       style="width:16px;height:16px;cursor:pointer;">

                                <span style="color:#fff;font-size:13px;">${escapeHtml(entry.label)}</span>

                            </label>

                        `).join('')}

                    </div>

                </div>

                <div style="display:none;margin-bottom:14px;" data-sb2-download-progress>

                    <div style="font-size:12px;color:#8d8d8d;margin-bottom:5px;">

                        下载进度：<span data-sb2-download-progress-text>0/0</span>

                    </div>

                    <div style="width:100%;height:4px;background:#1a1a1a;border-radius:2px;overflow:hidden;">

                        <div data-sb2-download-progress-bar style="height:100%;background:#4caf50;width:0%;transition:width .3s;"></div>

                    </div>

                </div>

                <div class="modal-form-actions">

                    <button class="secondary-button" type="button" data-sb2-download-cancel>取消</button>

                    <button class="primary-button" type="button" data-sb2-download-confirm>开始下载</button>

                </div>

            </div>

        </div>

    `;



    document.body.appendChild(modal);



    const backdrop = modal.querySelector('.modal-backdrop');

    const closeBtn = modal.querySelector('.modal-close');

    const cancelBtn = modal.querySelector('[data-sb2-download-cancel]');

    const confirmBtn = modal.querySelector('[data-sb2-download-confirm]');

    const selectAllBtn = modal.querySelector('[data-sb2-download-select-all]');

    const unselectAllBtn = modal.querySelector('[data-sb2-download-unselect-all]');

    const progressWrap = modal.querySelector('[data-sb2-download-progress]');



    const close = () => closeStoryboard2BatchDownloadModal();

    backdrop?.addEventListener('click', close);

    closeBtn?.addEventListener('click', close);

    cancelBtn?.addEventListener('click', close);



    selectAllBtn?.addEventListener('click', () => {

        modal.querySelectorAll('.sb2-batch-download-checkbox').forEach(checkbox => {

            checkbox.checked = true;

        });

    });

    unselectAllBtn?.addEventListener('click', () => {

        modal.querySelectorAll('.sb2-batch-download-checkbox').forEach(checkbox => {

            checkbox.checked = false;

        });

    });



    confirmBtn?.addEventListener('click', async () => {

        const selected = Array.from(modal.querySelectorAll('.sb2-batch-download-checkbox:checked'));

        if (selected.length === 0) {

            showToast('请至少选择一个分镜', 'warning');

            return;

        }



        confirmBtn.disabled = true;

        cancelBtn.disabled = true;

        progressWrap.style.display = 'block';

        updateStoryboard2BatchDownloadProgress(modal, 0, selected.length);



        let successCount = 0;

        for (let index = 0; index < selected.length; index += 1) {

            const checkbox = selected[index];

            const fileUrl = checkbox.dataset.fileUrl || '';

            const fileName = checkbox.dataset.fileName || `download_${index + 1}`;

            const label = checkbox.dataset.fileLabel || `分镜${index + 1}`;

            try {

                await downloadFileWithCustomName(fileUrl, fileName);

                successCount += 1;

            } catch (error) {

                console.error(`Failed to download storyboard2 ${type}:`, error);

                showToast(`${label} 下载失败: ${error.message}`, 'error');

            }

            updateStoryboard2BatchDownloadProgress(modal, index + 1, selected.length);

        }



        if (successCount === selected.length) {

            showToast(`下载完成：${successCount}/${selected.length}`, 'success');

        } else {

            showToast(`下载完成：成功 ${successCount}，失败 ${selected.length - successCount}`, 'warning');

        }



        confirmBtn.disabled = false;

        cancelBtn.disabled = false;

    });

}



function storyboard2SizeToAspectRatio(size) {

    const normalized = normalizeShotImageSize(size);

    const [w, h] = normalized.split(':').map(Number);

    if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) {

        return '9 / 16';

    }

    return `${w} / ${h}`;

}



function openStoryboard2ImageSizeModal(shotIndex, rowIndex) {

    closeStoryboard2ImageSizeModal();



    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'storyboard2ImageSizeModal';

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="closeStoryboard2ImageSizeModal()"></div>

        <div class="modal-content shot-image-size-modal">

            <div class="modal-header">

                <h3>生成镜头图</h3>

                <button class="modal-close" onclick="closeStoryboard2ImageSizeModal()">&times;</button>

            </div>

            <div class="modal-body shot-image-size-modal-body">

                <div class="form-group shot-image-size-group">

                    <label class="form-label">画面比例</label>

                    <select id="storyboard2ImageSizeSelect" class="form-input shot-image-size-select">

                        ${getShotImageSizeOptionsHtml('9:16')}

                    </select>

                </div>

                <div class="shot-image-size-actions">

                    <button class="secondary-button shot-image-size-btn" onclick="closeStoryboard2ImageSizeModal()">取消</button>

                    <button class="primary-button shot-image-size-btn" onclick="confirmStoryboard2RowImageGeneration(${shotIndex}, ${rowIndex})">开始生成</button>

                </div>

            </div>

        </div>

    `;



    document.body.appendChild(modal);

}



function closeStoryboard2ImageSizeModal() {

    const modal = document.getElementById('storyboard2ImageSizeModal');

    if (modal) {

        modal.remove();

    }

}



function confirmStoryboard2RowImageGeneration(shotIndex, rowIndex) {

    const selected = document.getElementById('storyboard2ImageSizeSelect')?.value || '9:16';

    const size = normalizeShotImageSize(selected);

    closeStoryboard2ImageSizeModal();

    triggerStoryboard2RowImageGeneration(shotIndex, rowIndex, size);

}



function markStoryboard2SubShotPromptOriginal(textarea) {

    if (!textarea) {

        return;

    }

    textarea.dataset.originalValue = textarea.value || '';

}



function getStoryboard2SubShotByPosition(shotIndex, rowIndex) {

    const state = ensureStoryboard2EpisodeState();

    if (!state) {

        return null;

    }



    const shot = state.boardData.shots?.[shotIndex];

    const row = shot?.sub_shots?.[rowIndex];

    if (!shot || !row) {

        return null;

    }



    return { state, shot, row };

}



async function saveStoryboard2SubShotPromptOnBlur(event, shotIndex, rowIndex) {

    const textarea = event?.target;

    if (!textarea) {

        return;

    }



    const currentValue = textarea.value || '';

    const originalValue = textarea.dataset.originalValue || '';

    if (currentValue === originalValue) {

        return;

    }



    const ok = await saveStoryboard2SubShotPrompt(shotIndex, rowIndex, currentValue, textarea);

    if (ok) {

        textarea.dataset.originalValue = textarea.value || '';

    }

}



async function saveStoryboard2SubShotPrompt(shotIndex, rowIndex, promptText, textarea = null) {

    const position = getStoryboard2SubShotByPosition(shotIndex, rowIndex);

    if (!position) {

        return false;

    }



    const { row } = position;

    if (!row.id) {

        showToast('分镜ID缺失，无法保存描述', 'error');

        return false;

    }



    APP_STATE.storyboard2SavingPromptBySubShot = APP_STATE.storyboard2SavingPromptBySubShot || {};

    if (APP_STATE.storyboard2SavingPromptBySubShot[row.id]) {

        return false;

    }

    APP_STATE.storyboard2SavingPromptBySubShot[row.id] = true;



    if (textarea) {

        textarea.classList.add('is-saving');

    }



    try {

        const response = await apiRequest(`/api/storyboard2/subshots/${row.id}`, {

            method: 'PATCH',

            body: JSON.stringify({

                sora_prompt: promptText

            })

        });



        let result = null;

        try {

            result = await response.json();

        } catch (error) {

            result = null;

        }



        if (!response.ok) {

            throw new Error(result?.detail || '分镜描述保存失败');

        }



        const savedPrompt = typeof result?.sora_prompt === 'string' ? result.sora_prompt : (promptText || '');

        row.sora_prompt = savedPrompt;

        if (textarea) {

            textarea.value = savedPrompt;

        }



        return true;

    } catch (error) {

        console.error('Failed to save storyboard2 subshot prompt:', error);

        showToast(`分镜描述保存失败: ${error.message}`, 'error');

        return false;

    } finally {

        delete APP_STATE.storyboard2SavingPromptBySubShot[row.id];

        if (textarea) {

            textarea.classList.remove('is-saving');

        }

    }

}



async function saveStoryboard2SubShotSceneOnBlur(event, shotIndex, rowIndex) {

    const textarea = event?.target;

    if (!textarea) {

        return;

    }



    const currentValue = textarea.value || '';

    const originalValue = textarea.dataset.originalValue || '';

    if (currentValue === originalValue) {

        return;

    }



    const ok = await saveStoryboard2SubShotScene(shotIndex, rowIndex, currentValue, textarea);

    if (ok) {

        textarea.dataset.originalValue = textarea.value || '';

    }

}



async function saveStoryboard2SubShotScene(shotIndex, rowIndex, sceneText, textarea = null) {

    const position = getStoryboard2SubShotByPosition(shotIndex, rowIndex);

    if (!position) {

        return false;

    }



    const { row } = position;

    if (!row.id) {

        showToast('分镜ID缺失，无法保存场景描述', 'error');

        return false;

    }



    APP_STATE.storyboard2SavingSceneBySubShot = APP_STATE.storyboard2SavingSceneBySubShot || {};

    if (APP_STATE.storyboard2SavingSceneBySubShot[row.id]) {

        return false;

    }

    APP_STATE.storyboard2SavingSceneBySubShot[row.id] = true;



    if (textarea) {

        textarea.classList.add('is-saving');

    }



    try {

        const response = await apiRequest(`/api/storyboard2/subshots/${row.id}`, {

            method: 'PATCH',

            body: JSON.stringify({

                scene_override: sceneText

            })

        });



        let result = null;

        try {

            result = await response.json();

        } catch (error) {

            result = null;

        }



        if (!response.ok) {

            throw new Error(result?.detail || '场景描述保存失败');

        }



        const savedText = typeof result?.scene_override === 'string' ? result.scene_override : (sceneText || '');

        row.scene_override = savedText;

        if (textarea) {

            textarea.value = savedText;

        }

        return true;

    } catch (error) {

        console.error('Failed to save storyboard2 subshot scene override:', error);

        showToast(`场景描述保存失败: ${error.message}`, 'error');

        return false;

    } finally {

        delete APP_STATE.storyboard2SavingSceneBySubShot[row.id];

        if (textarea) {

            textarea.classList.remove('is-saving');

        }

    }

}



async function triggerStoryboard2RowImageGeneration(shotIndex, rowIndex, selectedSize = null) {

    const state = ensureStoryboard2EpisodeState();

    if (!state) {

        return;

    }



    const shot = state.boardData.shots[shotIndex];

    const row = shot?.sub_shots?.[rowIndex];

    if (!row) {

        return;

    }



    if (!row.id) {

        showToast('分镜ID缺失，无法生成镜头图', 'error');

        return;

    }



    const subShotId = row.id;

    APP_STATE.storyboard2GeneratingBySubShot = APP_STATE.storyboard2GeneratingBySubShot || {};

    if (APP_STATE.storyboard2GeneratingBySubShot[subShotId]) {

        return;

    }



    const startScrollState = captureStoryboard2ScrollState();

    APP_STATE.storyboard2GeneratingBySubShot[subShotId] = true;

    renderStoryboard2Layout();

    restoreStoryboard2ScrollState(startScrollState);



    const imageSize = normalizeShotImageSize(selectedSize || getEpisodeShotImageSize());

    await ensureImageModelCatalogLoaded();

    const imageSelection = getDefaultImageSelection(

        IMAGE_MODEL_CATALOG,

        getEpisodeDetailImagesProvider() || null,

        getEpisodeDetailImagesModel()

    );



    try {

        const response = await apiRequest(`/api/storyboard2/subshots/${subShotId}/generate-images`, {

            method: 'POST',

            body: JSON.stringify({

                provider: imageSelection.provider,

                model: imageSelection.model,

                size: imageSize,

                resolution: imageSelection.resolution

            })

        });



        let result = null;

        try {

            result = await response.json();

        } catch (error) {

            result = null;

        }



        if (!response.ok) {

            throw new Error(result?.detail || '镜头图生成失败');

        }



        const beforeRefreshScrollState = captureStoryboard2ScrollState();

        await refreshStoryboard2BoardDataAndRender(beforeRefreshScrollState);

        startStoryboard2GenerationPolling();

        showToast(result?.message || '镜头图生成任务已启动', 'success');

    } catch (error) {

        console.error('Failed to generate storyboard2 images:', error);

        showToast(`生成失败: ${error.message}`, 'error');

    } finally {

        const beforeFinalRenderScrollState = captureStoryboard2ScrollState();

        delete APP_STATE.storyboard2GeneratingBySubShot[subShotId];

        renderStoryboard2Layout();

        restoreStoryboard2ScrollState(beforeFinalRenderScrollState);

    }

}



async function triggerStoryboard2RowVideoGeneration(shotIndex, rowIndex) {

    const state = ensureStoryboard2EpisodeState();

    if (!state) {

        return;

    }



    const shot = state.boardData.shots[shotIndex];

    const row = shot?.sub_shots?.[rowIndex];

    if (!row) {

        return;

    }



    if (!row.id) {

        showToast('分镜ID缺失，无法生成视频', 'error');

        return;

    }



    const subShotId = row.id;

    APP_STATE.storyboard2VideoGeneratingBySubShot = APP_STATE.storyboard2VideoGeneratingBySubShot || {};

    if (APP_STATE.storyboard2VideoGeneratingBySubShot[subShotId]) {

        return;

    }



    const startScrollState = captureStoryboard2ScrollState();

    APP_STATE.storyboard2VideoGeneratingBySubShot[subShotId] = true;

    renderStoryboard2Layout();

    restoreStoryboard2ScrollState(startScrollState);



    const imageSize = getEpisodeShotImageSize();

    const videoDuration = getEpisodeStoryboard2VideoDuration();



    try {

        const response = await apiRequest(`/api/storyboard2/subshots/${subShotId}/generate-video`, {

            method: 'POST',

            body: JSON.stringify({

                aspect_ratio: imageSize,

                duration: videoDuration

            })

        });



        let result = null;

        try {

            result = await response.json();

        } catch (error) {

            result = null;

        }



        if (!response.ok) {

            throw new Error(result?.detail || '视频生成失败');

        }



        const beforeRefreshScrollState = captureStoryboard2ScrollState();

        await refreshStoryboard2BoardDataAndRender(beforeRefreshScrollState);

        startStoryboard2GenerationPolling();

        showToast(result?.message || '视频生成任务已启动', 'success');

    } catch (error) {

        console.error('Failed to generate storyboard2 video:', error);

        showToast(`生成失败: ${error.message}`, 'error');

    } finally {

        const beforeFinalRenderScrollState = captureStoryboard2ScrollState();

        delete APP_STATE.storyboard2VideoGeneratingBySubShot[subShotId];

        renderStoryboard2Layout();

        restoreStoryboard2ScrollState(beforeFinalRenderScrollState);

    }

}



async function deleteStoryboard2CandidateImage(event, shotIndex, rowIndex, candidateIndex) {

    if (event) {

        event.preventDefault();

        event.stopPropagation();

    }



    const state = ensureStoryboard2EpisodeState();

    if (!state) {

        return;

    }



    const shot = state.boardData.shots?.[shotIndex];

    const row = shot?.sub_shots?.[rowIndex];

    const candidate = row?.candidates?.[candidateIndex];

    if (!candidate?.id) {

        return;

    }



    const confirmed = await showConfirmModal('确定删除这张可选图片吗？');

    if (!confirmed) {

        return;

    }



    APP_STATE.storyboard2DeletingByImage = APP_STATE.storyboard2DeletingByImage || {};

    if (APP_STATE.storyboard2DeletingByImage[candidate.id]) {

        return;

    }



    APP_STATE.storyboard2DeletingByImage[candidate.id] = true;

    renderStoryboard2Layout();



    try {

        const response = await apiRequest(`/api/storyboard2/images/${candidate.id}`, {

            method: 'DELETE'

        });



        let result = null;

        try {

            result = await response.json();

        } catch (error) {

            result = null;

        }



        if (!response.ok) {

            throw new Error(result?.detail || '删除失败');

        }



        await refreshStoryboard2BoardDataAndRender();

        showToast('可选图片已删除', 'success');

    } catch (error) {

        console.error('Failed to delete storyboard2 image:', error);

        showToast(`删除失败: ${error.message}`, 'error');

    } finally {

        delete APP_STATE.storyboard2DeletingByImage[candidate.id];

        renderStoryboard2Layout();

    }

}



async function deleteStoryboard2Video(event, shotIndex, rowIndex, videoIndex) {

    if (event) {

        event.preventDefault();

        event.stopPropagation();

    }



    const state = ensureStoryboard2EpisodeState();

    if (!state) {

        return;

    }



    const shot = state.boardData.shots?.[shotIndex];

    const row = shot?.sub_shots?.[rowIndex];

    const video = row?.videos?.[videoIndex];

    if (!video?.id) {

        return;

    }



    const confirmed = await showConfirmModal('确定删除这个视频吗？');

    if (!confirmed) {

        return;

    }



    APP_STATE.storyboard2DeletingByVideo = APP_STATE.storyboard2DeletingByVideo || {};

    if (APP_STATE.storyboard2DeletingByVideo[video.id]) {

        return;

    }



    APP_STATE.storyboard2DeletingByVideo[video.id] = true;

    renderStoryboard2Layout();



    try {

        const response = await apiRequest(`/api/storyboard2/videos/${video.id}`, {

            method: 'DELETE'

        });



        let result = null;

        try {

            result = await response.json();

        } catch (error) {

            result = null;

        }



        if (!response.ok) {

            throw new Error(result?.detail || '删除失败');

        }



        await refreshStoryboard2BoardDataAndRender();

        showToast('视频已删除', 'success');

    } catch (error) {

        console.error('Failed to delete storyboard2 video:', error);

        showToast(`删除失败: ${error.message}`, 'error');

    } finally {

        delete APP_STATE.storyboard2DeletingByVideo[video.id];

        renderStoryboard2Layout();

    }

}



function handleStoryboard2CandidateDragStart(event, shotIndex, rowIndex, candidateIndex) {

    APP_STATE.storyboard2DragSource = { shotIndex, rowIndex, candidateIndex };

    startStoryboard2DragAutoScroll(event.clientY);

    if (event.dataTransfer) {

        event.dataTransfer.effectAllowed = 'copy';

        event.dataTransfer.setData('text/plain', JSON.stringify(APP_STATE.storyboard2DragSource));

    }

}



function handleStoryboard2CandidateDragEnd() {

    APP_STATE.storyboard2DragSource = null;

    stopStoryboard2DragAutoScroll();

}



function handleStoryboard2CurrentDragOver(event) {

    event.preventDefault();

    maybeAutoScrollStoryboard2Rows(event.clientY);

    if (event.currentTarget) {

        event.currentTarget.classList.add('drag-over');

    }

}



function handleStoryboard2CurrentDragLeave(event) {

    if (event.currentTarget) {

        event.currentTarget.classList.remove('drag-over');

    }

}



async function handleStoryboard2CurrentDrop(event, targetShotIndex, targetRowIndex) {

    event.preventDefault();

    if (event.currentTarget) {

        event.currentTarget.classList.remove('drag-over');

    }



    const source = APP_STATE.storyboard2DragSource;

    APP_STATE.storyboard2DragSource = null;

    if (!source) {

        stopStoryboard2DragAutoScroll();

        return;

    }



    const state = ensureStoryboard2EpisodeState();

    if (!state) {

        stopStoryboard2DragAutoScroll();

        return;

    }



    const sourceShot = state.boardData.shots[source.shotIndex];

    const sourceRow = sourceShot?.sub_shots?.[source.rowIndex];

    const sourceCandidate = sourceRow?.candidates?.[source.candidateIndex];

    const targetShot = state.boardData.shots[targetShotIndex];

    const targetRow = targetShot?.sub_shots?.[targetRowIndex];



    if (!sourceCandidate || !sourceCandidate.id || !targetRow || !targetRow.id) {

        stopStoryboard2DragAutoScroll();

        return;

    }



    try {

        const response = await apiRequest(`/api/storyboard2/subshots/${targetRow.id}/current-image`, {

            method: 'PATCH',

            body: JSON.stringify({

                current_image_id: sourceCandidate.id

            })

        });



        let result = null;

        try {

            result = await response.json();

        } catch (error) {

            result = null;

        }



        if (!response.ok) {

            throw new Error(result?.detail || '设置当前图失败');

        }



        await refreshStoryboard2BoardDataAndRender();

        showToast('已将候选图设置为当前图', 'success');

    } catch (error) {

        console.error('Failed to set storyboard2 current image:', error);

        showToast(`设置失败: ${error.message}`, 'error');

    } finally {

        stopStoryboard2DragAutoScroll();

    }

}



function handleStoryboard2GlobalDragOver(event) {

    if (!APP_STATE.storyboard2DragSource) {

        return;

    }

    maybeAutoScrollStoryboard2Rows(event.clientY);

}



function startStoryboard2DragAutoScroll(initialY) {

    APP_STATE.storyboard2DragY = Number(initialY) || 0;

    if (APP_STATE.storyboard2AutoScrollActive) {

        return;

    }

    APP_STATE.storyboard2AutoScrollActive = true;

    document.addEventListener('dragover', handleStoryboard2GlobalDragOver);

}



function stopStoryboard2DragAutoScroll() {

    APP_STATE.storyboard2AutoScrollActive = false;

    APP_STATE.storyboard2DragY = null;

    document.removeEventListener('dragover', handleStoryboard2GlobalDragOver);

}



function maybeAutoScrollStoryboard2Rows(clientY) {

    const rowsContainer = document.querySelector('.storyboard2-rows');

    if (!rowsContainer) {

        return;

    }



    const y = Number(clientY);

    if (Number.isNaN(y)) {

        return;

    }



    const rect = rowsContainer.getBoundingClientRect();

    const threshold = 90;

    const maxStep = 30;



    if (y < rect.top + threshold) {

        const ratio = (rect.top + threshold - y) / threshold;

        const delta = Math.min(maxStep, Math.max(6, Math.round(maxStep * ratio)));

        rowsContainer.scrollTop -= delta;

        return;

    }



    if (y > rect.bottom - threshold) {

        const ratio = (y - (rect.bottom - threshold)) / threshold;

        const delta = Math.min(maxStep, Math.max(6, Math.round(maxStep * ratio)));

        rowsContainer.scrollTop += delta;

    }

}



function previewStoryboard2CandidateImage(shotIndex, rowIndex, candidateIndex) {

    const state = ensureStoryboard2EpisodeState();

    if (!state) {

        return;

    }



    const shot = state.boardData.shots[shotIndex];

    const row = shot?.sub_shots?.[rowIndex];

    const candidate = row?.candidates?.[candidateIndex];

    if (!candidate) {

        return;

    }



    openStoryboard2ImagePreviewModal(candidate.image_url, `${shot.shot_label} · 分镜${row.order} · ${candidate.label}`);

}



function previewStoryboard2CurrentImage(shotIndex, rowIndex) {

    const state = ensureStoryboard2EpisodeState();

    if (!state) {

        return;

    }



    const shot = state.boardData.shots[shotIndex];

    const row = shot?.sub_shots?.[rowIndex];

    const image = row?.current_image;

    if (!image) {

        showToast('当前图为空，请先拖拽候选图到当前图区', 'info');

        return;

    }



    openStoryboard2ImagePreviewModal(image.image_url, `${shot.shot_label} · 分镜${row.order} · 当前图`);

}



function openStoryboard2ImagePreviewModal(imageUrl, title) {

    closeStoryboard2ImagePreviewModal();



    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'storyboard2PreviewModal';

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="closeStoryboard2ImagePreviewModal()"></div>

        <div class="modal-content" style="max-width: 900px; width: 92vw; padding: 16px; background: #0f0f0f; border: 1px solid #2a2a2a;">

            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">

                <div style="font-size: 14px; color: #fff; font-weight: 600;">${escapeHtml(title || '图片预览')}</div>

                <button class="modal-close" onclick="closeStoryboard2ImagePreviewModal()">&times;</button>

            </div>

            <div style="background: #050505; border: 1px solid #222; border-radius: 6px; padding: 10px;">

                <img src="${escapeHtml(imageUrl)}" alt="preview" style="width: 100%; max-height: 70vh; object-fit: contain; display: block;" />

            </div>

        </div>

    `;



    document.body.appendChild(modal);

}



function closeStoryboard2ImagePreviewModal() {

    const modal = document.getElementById('storyboard2PreviewModal');

    if (modal) {

        modal.remove();

    }

}



// Render storyboard shots grid

function getShotLabel(shot) {

    if (!shot) return '';

    const base = shot.shot_number;

    const variant = shot.variant_index || 0;

    return variant > 0 ? `${base}_${variant}` : `${base}`;

}



function getShotDisplayTaskId(shot) {

    if (!shot) return '';

    return String(shot.task_id || shot.managed_task_id || '').trim();

}



function getShotCancelableVideoTaskId(shot) {

    if (!shot) return '';

    return String(shot.task_id || '').trim();

}



function getShotVideoStatusInteractionMeta(shot, options = {}) {

    if (!shot) {

        return { inlineAttrs: '', cursor: '', onClick: null };

    }



    const stopPropagation = options.stopPropagation !== false;

    const stopPrefix = stopPropagation ? 'event.stopPropagation(); ' : '';

    const videoStatus = String(shot.video_status || '').trim();



    if (videoStatus === 'processing' && shot.id) {

        const shotId = Number(shot.id);

        return {

            inlineAttrs: `onclick="${stopPrefix}showVideoProcessingInfo(${shotId})" style="cursor: pointer;"`,

            cursor: 'pointer',

            onClick: function(e) {

                if (stopPropagation && e) {

                    e.stopPropagation();

                }

                showVideoProcessingInfo(shotId);

            }

        };

    }



    if (videoStatus === 'failed') {

        const rawVideo = String(shot.thumbnail_video_path || shot.video_path || '').trim();

        let failReason = String(shot.video_error_message || '').trim();

        if (!failReason && rawVideo.startsWith('error:')) {

            failReason = rawVideo.substring(6);

        }

        if (!failReason) {

            return { inlineAttrs: '', cursor: '', onClick: null };

        }

        const escapedReason = escapeHtml(failReason).replace(/'/g, '&#39;');

        return {

            inlineAttrs: `onclick="${stopPrefix}showVideoErrorModal('${escapedReason}')" style="cursor: pointer;"`,

            cursor: 'pointer',

            onClick: function(e) {

                if (stopPropagation && e) {

                    e.stopPropagation();

                }

                showVideoErrorModal(failReason);

            }

        };

    }



    return { inlineAttrs: '', cursor: '', onClick: null };

}



function updateShotInState(shotId, updates) {

    if (!shotId) return;

    const index = APP_STATE.shots.findIndex(s => s.id === shotId);

    if (index >= 0) {

        APP_STATE.shots[index] = { ...APP_STATE.shots[index], ...updates };

    }

    if (APP_STATE.currentShot && APP_STATE.currentShot.id === shotId) {

        APP_STATE.currentShot = { ...APP_STATE.currentShot, ...updates };

    }

}



function getShotDetailImagePreviewPath(shot) {

    const previewPath = (shot?.detail_images_preview_path || '').trim();

    if (!previewPath || previewPath.startsWith('error:')) {

        return '';

    }

    return previewPath;

}


function getShotCardPreviewImageUrl(shot) {

    const firstFrameImageUrl = String(shot?.first_frame_reference_image_url || '').trim();

    if (firstFrameImageUrl && !firstFrameImageUrl.startsWith('error:')) {

        return firstFrameImageUrl;

    }

    const storyboardImagePath = String(shot?.storyboard_image_path || '').trim();

    if (storyboardImagePath && !storyboardImagePath.startsWith('error:')) {

        return storyboardImagePath;

    }

    const detailPreviewPath = String(shot?.detail_images_preview_path || '').trim();

    if (detailPreviewPath && !detailPreviewPath.startsWith('error:')) {

        return detailPreviewPath;

    }

    return '';

}


function getShotCardPreviewOverlayText(shot) {

    const detailImagesStatus = String(shot?.detail_images_status || 'idle').trim();

    if (detailImagesStatus === 'processing') {

        const progressText = String(shot?.detail_images_progress || '').trim();

        return progressText ? `镜头图生成中 ${progressText}` : '镜头图生成中';

    }

    return '';

}


function getShotImageViewerInitialUrl(shot, detailImagesPayload) {

    const candidates = buildShotFirstFrameReferenceCandidates(shot, detailImagesPayload);

    if (!Array.isArray(candidates) || candidates.length === 0) {

        return '';

    }

    const selectedFirstFrameUrl = String(
        detailImagesPayload?.first_frame_reference_image_url
        || shot?.first_frame_reference_image_url
        || ''
    ).trim();

    if (selectedFirstFrameUrl && candidates.some(item => item.image_url === selectedFirstFrameUrl)) {

        return selectedFirstFrameUrl;

    }

    const coverImageUrl = String(
        detailImagesPayload?.cover_image_url
        || shot?.storyboard_image_path
        || ''
    ).trim();

    if (coverImageUrl && candidates.some(item => item.image_url === coverImageUrl)) {

        return coverImageUrl;

    }

    return String(candidates[0]?.image_url || '').trim();

}


function buildShotCardPreviewImageHtml(imageUrl, overlayText = '') {

    const safeImageUrl = String(imageUrl || '').trim();

    if (!safeImageUrl) {

        return '';

    }

    const overlayHtml = overlayText
        ? `

            <div style="position: absolute; left: 6px; right: 6px; bottom: 6px; padding: 4px 6px; border-radius: 6px; background: rgba(0, 0, 0, 0.72); color: #ffa726; font-size: 11px; line-height: 1.3; text-align: center; z-index: 2;">

                ${escapeHtml(overlayText)}

            </div>

        `
        : '';

    return `

        <div style="position: relative; width: 100%; height: 100%;">

            <img src="${escapeHtml(safeImageUrl)}" style="width: 100%; height: 100%; object-fit: contain;" />

            ${overlayHtml}

        </div>

    `;

}



function buildShotVideoActionButtonsHtml(shot) {

    const shotId = shot?.id;

    const statusClass = shot?.video_status || 'idle';

    if (statusClass === 'completed') {

        return `<button class="shot-card-btn-link" onclick="event.stopPropagation(); exportVideo(${shotId})">下载视频</button>
               <button class="shot-card-btn-link" onclick="event.stopPropagation(); regenerateVideoForShot(${shotId})">重新生成</button>`;

    }

    if (statusClass === 'processing' || statusClass === 'submitting' || statusClass === 'preparing') {

        const cancelButtonHtml = getShotCancelableVideoTaskId(shot)
            ? `<button class="shot-card-btn-link" onclick="cancelVideoGenerationForShot(event, ${shotId})">取消生成</button>`
            : '';

        return `${cancelButtonHtml}
               <button class="shot-card-btn-link" onclick="event.stopPropagation(); regenerateVideoForShot(${shotId})">重新生成</button>`;

    }

    return `<button class="shot-card-btn-link" onclick="event.stopPropagation(); generateVideoForShot(${shotId})">生成视频</button>`;

}



// 增量更新单个镜头卡片

function updateShotCardInDOM(shot) {

    const card = document.querySelector(`[data-shot-id="${shot.id}"]`);

    if (!card) return; // 卡片不存鍦紝闇€要完全重寤?



    const currentId = APP_STATE.currentShot ? APP_STATE.currentShot.id : null;

    const isActive = shot.id === currentId;

    const statusClass = shot.video_status || 'idle';

    const shotLabel = getShotLabel(shot);



    const statusTextMap = {

        idle: '未生成',

        submitting: '提交中',

        preparing: '准备中',

        processing: '生成中',

        completed: '已完成',

        failed: '失败'

    };

    const statusText = statusTextMap[statusClass] || '未生成';



    // 更新active class

    if (isActive && !card.classList.contains('active')) {

        card.classList.add('active');

    } else if (!isActive && card.classList.contains('active')) {

        card.classList.remove('active');

    }



    // 更新status class

    ['idle', 'submitting', 'preparing', 'processing', 'completed', 'failed'].forEach(status => {

        const className = `status-${status}`;

        if (status === statusClass) {

            if (!card.classList.contains(className)) {

                card.classList.add(className);

            }

        } else {

            card.classList.remove(className);

        }

    });



    // 更新镜头编号

    const numberEl = card.querySelector('.shot-card-number');

    if (numberEl) {

        const currentHtml = numberEl.innerHTML;

        const importBadgeMatch = currentHtml.match(/<span class="shot-card-import-badge".*?<\/span>/);

        const importBadgeHtml = importBadgeMatch ? importBadgeMatch[0] : '';

        const newHtml = `${escapeHtml(shotLabel)}${importBadgeHtml}`;

        if (numberEl.innerHTML !== newHtml) {

            numberEl.innerHTML = newHtml;

        }

    }



    // 更新鐘舵€佹枃鏈?

    const statusEl = card.querySelector('.shot-card-status');

    if (statusEl) {

        // 更新鐘舵€佹枃鏈?

        statusEl.textContent = statusText;

        // 更新status class

        statusEl.className = `shot-card-status status-${statusClass}`;



        const interactionMeta = getShotVideoStatusInteractionMeta(shot);

        statusEl.style.cursor = interactionMeta.cursor || '';

        statusEl.onclick = interactionMeta.onClick;

    }



    const actionsRightEl = card.querySelector('.shot-card-actions-right');

    if (actionsRightEl) {

        const actionsHtml = buildShotVideoActionButtonsHtml(shot);

        if (actionsRightEl.innerHTML !== actionsHtml) {

            actionsRightEl.innerHTML = actionsHtml;

        }

    }



    // 更新Sora提示词状鎬?

    const soraPromptStatusClass = shot.sora_prompt_status || 'idle';

    const soraStatusEl = card.querySelector('.shot-card-sora-status');



    if (soraPromptStatusClass === 'generating') {

        // 闇€要显示生成中鐘舵€?

        if (!soraStatusEl) {

            // 创建鐘舵€佸厓绱?

            const statusEl = card.querySelector('.shot-card-status');

            if (statusEl) {

                const soraSpan = document.createElement('span');

                soraSpan.className = `shot-card-sora-status status-${soraPromptStatusClass}`;

                soraSpan.textContent = '提示词生成中';

                statusEl.parentElement.insertBefore(soraSpan, statusEl);

            }

        } else {

            // 更新现有元素

            soraStatusEl.textContent = '提示词生成中';

            soraStatusEl.className = `shot-card-sora-status status-${soraPromptStatusClass}`;

        }

    } else if (soraPromptStatusClass === 'completed') {

        // 闇€要显示完成状鎬?

        if (!soraStatusEl) {

            const statusEl = card.querySelector('.shot-card-status');

            if (statusEl) {

                const soraSpan = document.createElement('span');

                soraSpan.className = `shot-card-sora-status status-${soraPromptStatusClass}`;

                soraSpan.textContent = '提示词生成完毕';

                statusEl.parentElement.insertBefore(soraSpan, statusEl);

            }

        } else {

            soraStatusEl.textContent = '提示词生成完毕';

            soraStatusEl.className = `shot-card-sora-status status-${soraPromptStatusClass}`;

        }

    } else if (soraPromptStatusClass === 'failed') {

        // 闇€要显示失败状鎬?

        if (!soraStatusEl) {

            const statusEl = card.querySelector('.shot-card-status');

            if (statusEl) {

                const soraSpan = document.createElement('span');

                soraSpan.className = `shot-card-sora-status status-${soraPromptStatusClass}`;

                soraSpan.textContent = '提示词失败';

                statusEl.parentElement.insertBefore(soraSpan, statusEl);

            }

        } else {

            soraStatusEl.textContent = '提示词失败';

            soraStatusEl.className = `shot-card-sora-status status-${soraPromptStatusClass}`;

        }

    } else {

        // idle，移除状态元绱?

        if (soraStatusEl) {

            soraStatusEl.remove();

        }

    }



    // 更新分镜图状态badge - 不再显示，直接移除所有现有状态元绱?

    const storyboardImageStatusEl = card.querySelector('.shot-card-storyboard-image-status');

    if (storyboardImageStatusEl) {

        storyboardImageStatusEl.remove();

    }



    // 更新棰勮区域

    const previewEl = card.querySelector('.shot-card-preview');

    if (previewEl) {

        // 获取分镜图信鎭?

        const storyboardImagePath = (shot.storyboard_image_path || '').trim();

        const hasStoryboardImage = storyboardImagePath && storyboardImagePath !== '' && !storyboardImagePath.startsWith('error:');

        const detailImagePreviewPath = getShotDetailImagePreviewPath(shot);

        const hasDetailImagePreview = Boolean(detailImagePreviewPath);

        const previewImageUrl = getShotCardPreviewImageUrl(shot);

        const hasPreviewImage = Boolean(previewImageUrl);

        const storyboardImageStatus = shot.storyboard_image_status || 'idle';



        // 获取瑙嗛信息

        const rawVideo = (shot.thumbnail_video_path || shot.video_path || '').trim();

        const thumbnail = rawVideo && !rawVideo.startsWith('error:') ? rawVideo : '';

        const hasVideo = Boolean(thumbnail);



        // 更新class

        ['idle', 'submitting', 'preparing', 'processing', 'completed', 'failed'].forEach(status => {

            const className = `status-${status}`;

            if (status === statusClass) {

                if (!previewEl.classList.contains(className)) {

                    previewEl.classList.add(className);

                }

            } else {

                previewEl.classList.remove(className);

            }

        });



        // 妫€查是否为split布局

        const splitContainer = previewEl.querySelector('.shot-card-preview-split');



        if (splitContainer) {

            // Split布局存在，更新左右两渚?

            const leftEl = splitContainer.querySelector('.shot-card-preview-left');

            const rightEl = splitContainer.querySelector('.shot-card-preview-right');



            // 更新左侧：分镜图（使用detail_images_status优先锛?

            if (leftEl) {

                // 优先使用detail_images_status，其次使用storyboardImageStatus

                const detailImagesStatus = shot.detail_images_status || 'idle';
                const overlayText = getShotCardPreviewOverlayText(shot);
                const canOpenDetailViewer = Boolean(hasDetailImagePreview || detailImagesStatus === 'completed' || (detailImagesStatus === 'processing' && hasPreviewImage));



                let storyboardImagePreview;

                if (hasPreviewImage) {

                    storyboardImagePreview = buildShotCardPreviewImageHtml(previewImageUrl, overlayText);

                } else if (detailImagesStatus === 'processing') {

                    // 显示生成进度

                    const progressText = shot.detail_images_progress || '生成中...';

                    storyboardImagePreview = `<span class="shot-card-preview-text" style="font-size: 11px; color: #ffa726;">${progressText}</span>`;

                } else if (detailImagesStatus === 'failed') {

                    storyboardImagePreview = `<span class="shot-card-preview-text" style="font-size: 11px; color: #f44336;">生成失败</span>`;

                } else if (detailImagesStatus === 'completed') {

                    // 显示完成时的文字

                    // 如果progressText为空锛岃明全部成功，显示"点击查看"

                    // 如果progressText不为空，说明部分失败，显绀哄"3/5"

                    const progressText = shot.detail_images_progress ? shot.detail_images_progress : '点击查看';

                    storyboardImagePreview = `<span class="shot-card-preview-text" style="font-size: 11px; color: #4ade80;">${progressText}</span>`;

                } else if (storyboardImageStatus === 'processing') {

                    storyboardImagePreview = `<span class="shot-card-preview-text" style="font-size: 11px;">生成中...</span>`;

                } else if (storyboardImageStatus === 'failed') {

                    storyboardImagePreview = `<span class="shot-card-preview-text" style="font-size: 11px; color: #f44336;">生成失败</span>`;

                } else {

                    storyboardImagePreview = `<span class="shot-card-preview-text" style="font-size: 11px;">暂无镜头图</span>`;

                }



                if (leftEl.innerHTML !== storyboardImagePreview) {

                    leftEl.innerHTML = storyboardImagePreview;

                }

                leftEl.className = `shot-card-preview-left ${hasPreviewImage ? 'has-image' : ''}`;

                leftEl.onclick = function(e) {

                    e.stopPropagation();

                    if (canOpenDetailViewer) {

                        openDetailImagesViewer(shot.id);

                    } else if (hasStoryboardImage) {

                        openStoryboardImageModal(shot.id);

                    }

                };

            }



            // 更新右侧锛氳棰?

            if (rightEl) {

                const videoPreview = hasVideo

                    ? `<video class="shot-card-video" src="${escapeHtml(thumbnail)}" preload="metadata" muted playsinline></video>`

                    : `<span class="shot-card-preview-text" style="font-size: 11px;">${escapeHtml(statusText)}</span>`;



                if (rightEl.innerHTML !== videoPreview) {

                    rightEl.innerHTML = videoPreview;

                    rightEl.className = `shot-card-preview-right ${hasVideo ? 'has-video' : ''}`;

                }

            }

        } else {

            // Track prompt task so polling detects completion even if AI responds faster than the shots refresh

            console.log('[增量更新] 棰勮区域结构不匹配，跳过更新（等待完全重建）');

        }

    }



    // 更新鎽樿区域锛堝终显示原剧本段落，不琚敊璇俊鎭盖）

    const summaryEl = card.querySelector('.shot-card-summary');

    if (summaryEl) {

        const excerpt = (shot.script_excerpt || '').trim();

        const summaryLabel = '原剧本段落';

        const summaryText = excerpt || '暂无内容';



        const labelEl = summaryEl.querySelector('.shot-card-label');

        const textEl = summaryEl.querySelector('.shot-card-text');



        if (labelEl && labelEl.textContent !== summaryLabel) {

            labelEl.textContent = summaryLabel;

        }



        if (textEl && textEl.textContent !== summaryText) {

            textEl.textContent = summaryText;

        }



        // 移除error-summary类（如果之前有）

        if (summaryEl.classList.contains('error-summary')) {

            summaryEl.classList.remove('error-summary');

        }

    }

}



function renderStoryboardShotsGrid(forceRebuild = false) {

    console.log('[renderStoryboardShotsGrid] ========== 寮€始调鐢紝forceRebuild=', forceRebuild);

    const stack = new Error().stack;

    console.log('[renderStoryboardShotsGrid] 调用堆栈:', stack.split('\n')[2]); // 打印调用来源



    const grid = document.getElementById('storyboardShotsGrid');

    if (!grid) {

        return;

    }



    if (!APP_STATE.shots || APP_STATE.shots.length === 0) {

        grid.innerHTML = '<div class="storyboard-empty-state">暂无镜头</div>';

        return;

    }



    // 妫€查是否有任何镜头正在生成镜头图，如果有则强制完全重建

    const hasDetailImagesGenerating = APP_STATE.shots.some(s => s.detail_images_status === 'processing' || s.detail_images_status === 'completed');

    if (hasDetailImagesGenerating) {

        forceRebuild = true;

    }



    // 妫€查是否需要完全重建（棣栨渲染或强制重建）

    const existingCards = grid.querySelectorAll('[data-shot-id]');

    const needsRebuild = forceRebuild || existingCards.length === 0 || existingCards.length !== APP_STATE.shots.length;



    if (needsRebuild) {

        // 完全重建DOM

        const currentId = APP_STATE.currentShot ? APP_STATE.currentShot.id : null;

        const statusTextMap = {

            idle: '未生成',

            submitting: '提交中',

            preparing: '准备中',

            processing: '生成中',

            completed: '已完成',

            failed: '失败'

        };

        const importBatches = getImportBatchesForEpisode(APP_STATE.currentEpisode);

        const batchIndexByShotId = new Map();

        importBatches.forEach((batch, index) => {

            (batch.shotIds || []).forEach(shotId => {

                if (!batchIndexByShotId.has(shotId)) {

                    batchIndexByShotId.set(shotId, index);

                }

            });

        });

        const mainShotIdByNumber = {};

        APP_STATE.shots.forEach(shot => {

            if (shot.variant_index === 0) {

                mainShotIdByNumber[shot.shot_number] = shot.id;

            }

        });



        grid.innerHTML = APP_STATE.shots.map(shot => {

            console.log(`[renderStoryboardShotsGrid] 渲染镜头 id=${shot.id}, detail_images_status=${shot.detail_images_status}`);

            const shotLabel = getShotLabel(shot);

            const statusClass = shot.video_status || 'idle';

            const statusText = statusTextMap[statusClass] || '未生成';



            // Sora提示词状态（如果瑙嗛已经生成过，说明涓€定有提示词）

            const soraPromptStatusClass = shot.sora_prompt_status || 'idle';

            const videoHasBeenGenerated = ['submitting', 'preparing', 'processing', 'completed', 'failed'].includes(statusClass);

            const soraPromptStatusText = soraPromptStatusClass === 'generating' ? '提示词生成中' :

                                        (soraPromptStatusClass === 'completed' || videoHasBeenGenerated) ? '提示词生成完毕' :

                                        soraPromptStatusClass === 'failed' ? '提示词失败' : '';



            // 分镜图状态（仅用于判鏂览显示，不在卡片上显示状态文字）

            const storyboardImageStatus = shot.storyboard_image_status || 'idle';



            const isActive = shot.id === currentId;

            const mainShotId = mainShotIdByNumber[shot.shot_number] || shot.id;

            const batchIndex = batchIndexByShotId.has(mainShotId) ? batchIndexByShotId.get(mainShotId) : null;

            const batchColor = batchIndex !== null

                ? IMPORT_BATCH_COLORS[batchIndex % IMPORT_BATCH_COLORS.length]

                : null;

            const importClass = batchIndex !== null ? ' import-batch' : '';

            const importStyle = batchColor ? ` style="--import-batch-color: ${batchColor};"` : '';

            const importBadgeHtml = batchIndex !== null

                ? `<span class="shot-card-import-badge" style="background: ${batchColor};">导入${batchIndex + 1}</span>`

                : '';



            // 优先使用 video_error_message 瀛楁显示閿欒信息

            const rawVideo = (shot.thumbnail_video_path || shot.video_path || '').trim();

            let failReason = '';

            if (statusClass === 'failed') {

                failReason = (shot.video_error_message || '').trim();

                // 如果新字段为空，鍏煎旧数鎹紙浠?video_path 解析锛?

                if (!failReason && rawVideo.startsWith('error:')) {

                    failReason = rawVideo.substring(6);

                }

            }



            // 始终显示原剧鏈落，涓嶈鐩?

            const excerpt = (shot.script_excerpt || '').trim();

            const summaryLabel = '原剧本段落';

            const summaryText = excerpt || '暂无内容';



            const thumbnail = rawVideo && !rawVideo.startsWith('error:') ? rawVideo : '';

            const hasVideo = Boolean(thumbnail);



            // 分镜鍥鹃览（现在改为镜头图）

            const storyboardImagePath = (shot.storyboard_image_path || '').trim();

            const hasStoryboardImage = storyboardImagePath && storyboardImagePath !== '' && !storyboardImagePath.startsWith('error:');

            const detailImagePreviewPath = getShotDetailImagePreviewPath(shot);

            const hasDetailImagePreview = Boolean(detailImagePreviewPath);

            const detailImagesStatus = shot.detail_images_status || 'idle';
            const previewImageUrl = getShotCardPreviewImageUrl(shot);
            const overlayText = getShotCardPreviewOverlayText(shot);

            const canOpenDetailViewer = Boolean(hasDetailImagePreview || detailImagesStatus === 'completed' || (detailImagesStatus === 'processing' && Boolean(previewImageUrl)));

            const hasPreviewImage = Boolean(previewImageUrl);



            // 使用detail_images_status来判鏂暅头图鐘舵€?

            console.log(`[renderStoryboardShotsGrid] 镜头 id=${shot.id}, detailImagesStatus=${detailImagesStatus}, hasStoryboardImage=${hasStoryboardImage}`);



            let storyboardImagePreview;

            if (hasPreviewImage) {

                console.log(`[renderStoryboardShotsGrid] 镜头 id=${shot.id} 进入分支: previewImage`);

                storyboardImagePreview = buildShotCardPreviewImageHtml(previewImageUrl, overlayText);

            } else if (detailImagesStatus === 'processing') {

                console.log(`[renderStoryboardShotsGrid] 镜头 id=${shot.id} 进入分支: processing`);

                storyboardImagePreview = `<span class="shot-card-preview-text" style="font-size: 11px; color: #ffa726;">镜头图生成中...</span>`;

            } else if (detailImagesStatus === 'failed') {

                console.log(`[renderStoryboardShotsGrid] 镜头 id=${shot.id} 进入分支: failed`);

                storyboardImagePreview = `<span class="shot-card-preview-text" style="font-size: 11px; color: #f44336;">生成失败</span>`;

            } else if (detailImagesStatus === 'completed') {

                console.log(`[renderStoryboardShotsGrid] 镜头 id=${shot.id} 进入分支: completed -> 点击查看`);

                // 已完成，显示绗竴张图片或提示

                storyboardImagePreview = `<span class="shot-card-preview-text" style="font-size: 11px; color: #4ade80;">点击查看</span>`;

            } else {

                console.log(`[renderStoryboardShotsGrid] 镜头 id=${shot.id} 进入分支: 默认 -> 暂无镜头图`);

                storyboardImagePreview = `<span class="shot-card-preview-text" style="font-size: 11px;">暂无镜头图</span>`;

            }



            const videoPreview = hasVideo

                ? `<video class="shot-card-video" src="${escapeHtml(thumbnail)}" preload="metadata" muted playsinline></video>`

                : `<span class="shot-card-preview-text" style="font-size: 11px;">${escapeHtml(statusText)}</span>`;



            const previewContent = `

                <div class="shot-card-preview-split">

                    <div class="shot-card-preview-left ${hasPreviewImage ? 'has-image' : ''}" onclick="event.stopPropagation(); ${canOpenDetailViewer ? `openDetailImagesViewer(${shot.id})` : (hasStoryboardImage ? `openStoryboardImageModal(${shot.id})` : '')}">

                        ${storyboardImagePreview}

                    </div>

                    <div class="shot-card-preview-right ${hasVideo ? 'has-video' : ''}" onclick="event.stopPropagation(); openShotVideoModal(${shot.id})">

                        ${videoPreview}

                    </div>

                </div>

            `;



            return `

                <div class="storyboard-shot-card ${isActive ? 'active' : ''} status-${statusClass}${importClass}"

                     data-shot-id="${shot.id}"

                     onclick="selectShot(${shot.id})"${importStyle}>

                    <div class="shot-card-header">

                        <div class="shot-card-number">${escapeHtml(shotLabel)}${importBadgeHtml}</div>

                        <div class="shot-card-header-actions">

                            <button class="shot-card-delete-btn" onclick="event.stopPropagation(); deleteShot(${shot.id})" title="删除镜头">

                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">

                                    <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/>

                                </svg>

                            </button>

                            ${soraPromptStatusText ? `<span class="shot-card-sora-status status-${soraPromptStatusClass}">${soraPromptStatusText}</span>` : ''}

                            <span class="shot-card-status status-${statusClass}" ${getShotVideoStatusInteractionMeta(shot).inlineAttrs}>${statusText}</span>

                        </div>

                    </div>

                    <div class="shot-card-preview status-${statusClass}">

                        ${previewContent}

                    </div>

                    <div class="shot-card-summary">

                        <div class="shot-card-label">${escapeHtml(summaryLabel)}</div>

                        <div class="shot-card-text">${escapeHtml(summaryText)}</div>

                    </div>

                    <div class="shot-card-actions-left">

                        <button class="shot-card-btn-link" onclick="event.stopPropagation(); openDetailImagesGenerateModal(${shot.id})">生成镜头图</button>

                    </div>

                    <div class="shot-card-actions-right">

                        ${buildShotVideoActionButtonsHtml(shot)}

                    </div>

                </div>

            `;

        }).join('');

    } else {

        // 增量更新：只更新变化的卡鐗?

        APP_STATE.shots.forEach(shot => {

            updateShotCardInDOM(shot);

        });

    }

}



async function selectShot(shotId) {

    try {

        // 鉁?主动从后绔幏取所有镜头的鏈€新数鎹?

        const shotsUrl = `/api/episodes/${APP_STATE.currentEpisode}/shots`;

        const response = await apiRequest(shotsUrl);



        if (response.ok) {

            const freshShots = await response.json();



            // 更新鏈湴缓存（所有镜头）

            APP_STATE.shots = freshShots;



            // 从最新数鎹腑找到当前选中的镜澶?

            APP_STATE.currentShot = freshShots.find(s => s.id === shotId);

        } else {

            // 如果请求失败，回閫€到本地缓瀛?

            APP_STATE.currentShot = APP_STATE.shots.find(s => s.id === shotId);

        }

    } catch (error) {

        console.error('Failed to fetch latest shots:', error);

        // 如果请求失败，回閫€到本地缓瀛?

        APP_STATE.currentShot = APP_STATE.shots.find(s => s.id === shotId);

    }



    APP_STATE.currentShotVideos = null;

    saveAppState();

    renderStoryboardShotsGrid(); // 使用增量更新更新active鐘舵€?

    renderStoryboardSidebar(); // 重建右侧栏显示新选中的镜澶?



    // 鉁?实时更新场景描述妗?

    updateSceneOverrideFromSelection();

}



// 鏂板镜头

async function addNewShot() {

    const maxNumber = Math.max(...APP_STATE.shots.map(s => s.shot_number), 0);
    const videoSettings = getEffectiveShotStoryboardVideoSettings(APP_STATE.currentShot);



    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/shots`, {

            method: 'POST',

            body: JSON.stringify({

                shot_number: maxNumber + 1,

                prompt_template: '',

                selected_card_ids: [],

                aspect_ratio: videoSettings.aspect_ratio,

                duration: videoSettings.duration

            })

        });



        if (response.ok) {

            const shotsUrl = `/api/episodes/${APP_STATE.currentEpisode}/shots`;

            const shotsResponse = await apiRequest(shotsUrl);

            APP_STATE.shots = await shotsResponse.json();

            APP_STATE.currentShot = APP_STATE.shots[APP_STATE.shots.length - 1];



            renderStoryboardShotsGrid(true); // 强制重建（镜头数量变化）

            renderStoryboardSidebar();

        }

    } catch (error) {

        console.error('Failed to add shot:', error);

        alert('新增失败');

    }

}



// 删除镜头

async function deleteShot(shotId) {

    const shot = APP_STATE.shots.find(s => s.id === shotId);

    if (!shot) return;



    const shotLabel = getShotLabel(shot);

    const confirmed = await showConfirmModal(

        `确定要删除镜头 #${shotLabel} 吗？此操作不可恢复。`,

        '删除镜头'

    );



    if (!confirmed) return;



    try {

        const response = await apiRequest(`/api/shots/${shotId}`, {

            method: 'DELETE'

        });



        if (response.ok) {

            // 重新加载镜头列表（根鎹綋前模式筛选）

            const shotsUrl = `/api/episodes/${APP_STATE.currentEpisode}/shots`;

            const shotsResponse = await apiRequest(shotsUrl);

            APP_STATE.shots = await shotsResponse.json();



            // 如果删除的是当前选中的镜头，清空选中鐘舵€?

            if (APP_STATE.currentShot?.id === shotId) {

                APP_STATE.currentShot = APP_STATE.shots.length > 0 ? APP_STATE.shots[0] : null;

            }



            renderStoryboardShotsGrid(true); // 强制重建（镜头数量变化）

            renderStoryboardSidebar();

            showToast('镜头已删除');

        } else {

            const error = await response.json();

            alert(`删除失败: ${error.detail || '未知错误'}`);

        }

    } catch (error) {

        console.error('Failed to delete shot:', error);

        alert('删除失败');

    }

}



// 导入新增内容（文案输入 + AI分析）

async function importNewContent() {

    const text = await showTextareaModal(

        '导入新增内容',

        '请输入要添加的文案内容',

        '',

        '输入新的文案内容，AI将自动分析并生成分镜表'

    );



    if (!text || !text.trim()) {

        return;

    }



    if (!APP_STATE.currentEpisode) {

        showToast('当前没有选中的片段', 'error');

        return;

    }



    showToast('正在AI分析文案...', 'info');



    try {

        // 调用 API，传递 append: true 参数

        const analyzeResponse = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/analyze-storyboard`, {

            method: 'POST',

            body: JSON.stringify({

                content: text.trim(),

                append: true  // 追加模式

            })

        });



        if (analyzeResponse && analyzeResponse.ok) {

            const result = await analyzeResponse.json();



            // API 返回 generating: true，需要轮询等待生成完成

            if (result.generating) {

                showToast('AI分析任务已启动，正在生成分镜表...', 'info');



                // 轮询等待生成完成，完成后自动刷新分镜表界面

                await waitForStoryboardGenerationAndRefresh();

            }

        } else if (analyzeResponse) {

            const error = await analyzeResponse.json();

            showToast(`AI分析失败: ${error.detail || '未知错误'}`, 'error');

        }

    } catch (error) {

        console.error('Failed to analyze new content:', error);

        showToast('AI分析失败：网络错误', 'error');

    }

}



// 查询任务状态

async function queryTaskStatus() {

    const taskId = await showInputModal(

        '查询任务状态',

        '请输入任务ID (task_id)',

        '',

        '例如：Abc123def456'

    );



    if (!taskId || !taskId.trim()) {

        return;

    }



    showToast('正在查询任务状态...', 'info');



    try {

        const response = await apiRequest(`/api/tasks/${taskId.trim()}/status`);



        if (response.ok) {

            const result = await response.json();



            // 提取指定字段

            const status = result.status || '未知';

            const progress = result.progress !== undefined ? result.progress : '未知';

            const prompt = result.prompt || '无';

            const createdAt = formatBackendUtcToBeijing(result.created_at, result.created_at || '未知');



            // 格式化显示内容

            const formattedContent = `状态: ${status}



进度: ${progress}${typeof progress === 'number' ? '%' : ''}



创建时间: ${createdAt}



提示词:

${prompt}`;



            // 使用textarea弹窗显示结果

            await showTextareaModal(

                '任务状态查询结果',

                `任务ID: ${taskId.trim()}`,

                formattedContent,

                '查询结果'

            );

        } else {

            const error = await response.json();

            showToast(`查询失败: ${error.detail || '未知错误'}`, 'error');

        }

    } catch (error) {

        console.error('Failed to query task status:', error);

        showToast('查询失败：网络错误', 'error');

    }

}



// 轮询等待分镜表生成完成，并刷新界面

async function waitForStoryboardGenerationAndRefresh() {

    const maxAttempts = 60; // 最多等待 60 次（约 5 分钟）

    let attempts = 0;



    const poll = async () => {

        attempts++;



        try {

            const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/storyboard`);

            const data = await response.json();



            // 如果还在生成中，继续轮询

            if (data.generating) {

                if (attempts >= maxAttempts) {

                    showToast('生成超时，请稍后刷新查看', 'warning');

                    return;

                }

                // 5 秒后再次检查

                setTimeout(poll, CREATE_FROM_STORYBOARD_POLL_INTERVAL_MS);

                return;

            }



            // 生成完成，获取最终的分镜表数据（后端已合并）

            const allShots = data.shots || [];

            const subjects = data.subjects || []; // 获取 subjects



            if (allShots.length === 0) {

                showToast('分镜表为空', 'warning');

                return;

            }



            // 保存 subjects 数据

            storyboardSubjects = subjects;



            showToast(`分镜表生成完成，共 ${allShots.length} 个镜头`, 'success');



            // 刷新分镜表界面

            await loadStoryboardTableStep();



        } catch (error) {

            console.error('Failed to poll storyboard status:', error);

            showToast('获取生成状态失败', 'error');

        }

    };



    // 开始轮询

    poll();

}



function extractTableFromSoraPrompt(fullPrompt) {

    /**

     * 从完整的sora_prompt涓彁取表格部分（去掉瑙嗛风格和场鏅弿述）

     *

     * fullPrompt格式示例锛?

     * 瑙嗛风格模板

     * 场景：xxx

     * 分镜表格锛?

     * | 时间 | 画面描述 | 台词与音鏁?|

     * | :--- | :--- | :--- |

     * ...

     *

     * 返回：仅表格部分

     */

    if (!fullPrompt || typeof fullPrompt !== 'string') {

        return '';

    }



    // 查找"分镜表格锛?鏍囪

    const tableMarker = '分镜表格：';

    const tableIndex = fullPrompt.indexOf(tableMarker);



    if (tableIndex !== -1) {

        // 提取表格部分（从"分镜表格锛?之后寮€始）

        return fullPrompt.substring(tableIndex + tableMarker.length).trim();

    }



    // 如果没有找到鏍囪锛屾查是否已经是表格格式（向后兼容）

    if (fullPrompt.includes('| 时间 |')) {

        return fullPrompt.trim();

    }



    // 否则返回原内容（向后鍏煎旧格式）

    return fullPrompt.trim();

}



function buildSoraPromptText(shot) {

    if (!shot) return '';

    const parts = [];

    const template = (
        APP_STATE.currentEpisodeInfo?.video_prompt_template
        || shot.prompt_template
        || ''
    ).trim();

    if (template) parts.push(template);

    const videoPrompt = (shot.storyboard_video_prompt || '').trim();

    if (videoPrompt) parts.push(`分镜视频提示词(storyboardVideoPrompt): ${videoPrompt}`);

    const audioPrompt = (shot.storyboard_audio_prompt || '').trim();

    if (audioPrompt) parts.push(`分镜音频提示词(storyboardAudioPrompt): ${audioPrompt}`);

    const dialoguePrompt = (shot.storyboard_dialogue || '').trim();

    if (dialoguePrompt) parts.push(`分镜台词 (storyboardDialogue): ${dialoguePrompt}`);

    return parts.join('\n').trim();

}



/**

 * 构建Sora提示词（仅表格部分，不包鍚棰戦格）

 * 用于编辑界面显示

 */

function buildSoraPromptTableOnly(shot) {

    if (!shot) return '';

    const parts = [];

    // 鉂?不包鍚?prompt_template锛堣棰戦格）

    const videoPrompt = (shot.storyboard_video_prompt || '').trim();

    if (videoPrompt) parts.push(`分镜视频提示词(storyboardVideoPrompt): ${videoPrompt}`);

    const audioPrompt = (shot.storyboard_audio_prompt || '').trim();

    if (audioPrompt) parts.push(`分镜音频提示词(storyboardAudioPrompt): ${audioPrompt}`);

    const dialoguePrompt = (shot.storyboard_dialogue || '').trim();

    if (dialoguePrompt) parts.push(`分镜台词 (storyboardDialogue): ${dialoguePrompt}`);

    return parts.join('\n').trim();

}



function getShotAliasMap(shot) {

    if (!shot || !APP_STATE.cards) return {};

    let selectedIds = [];

    try {

        selectedIds = JSON.parse(shot.selected_card_ids || '[]');

    } catch (error) {

        selectedIds = [];

    }



    const aliasMap = {};

    APP_STATE.cards.forEach(card => {

        if (!selectedIds.includes(card.id)) return;

        const alias = (card.alias || '').trim();

        if (alias && alias !== card.name) {

            aliasMap[card.name] = alias;

        }

    });

    return aliasMap;

}



function applyAliasReplacements(text, aliasMap) {

    if (!text) return text;

    const names = Object.keys(aliasMap || {});

    if (names.length === 0) return text;

    names.sort((a, b) => b.length - a.length);

    let result = text;

    names.forEach(name => {

        result = result.split(name).join(aliasMap[name]);

    });

    return result;

}



async function editPromptTemplate() {

    if (!APP_STATE.currentEpisode) return;

    try {

        const currentTemplate = APP_STATE.currentEpisodeInfo?.video_prompt_template || '';



        const newTemplate = await showTextareaModal(

            '画风模板设置',

            '设置视频画风模板（留空使用默认）',

            currentTemplate,

            '视频风格：逐帧动画，2D手绘动漫风格...'

        );



        if (newTemplate === null || newTemplate === currentTemplate) {

            return;

        }



        const saveResponse = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/storyboard-video-settings`, {

            method: 'PATCH',

            body: JSON.stringify({ video_prompt_template: newTemplate })

        });



        if (saveResponse.ok) {

            const updated = await saveResponse.json();

            APP_STATE.currentEpisodeInfo = {
                ...(APP_STATE.currentEpisodeInfo || {}),
                video_prompt_template: updated?.video_prompt_template || ''
            };

            showToast('画风模板已更新', 'success');

        } else {

            const error = await saveResponse.json();

            showToast(error.detail || '保存失败', 'error');

        }

    } catch (error) {

        console.error('Failed to update prompt template:', error);

        showToast('更新失败', 'error');

    }

}



async function editSoraPromptStyle() {

    if (!APP_STATE.currentScript) return;

    const currentStyleRaw = APP_STATE.soraPromptStyle || '';
    let currentStyle = currentStyleRaw;

    try {
        const defaultPromptConfig = await getPromptConfigByKey('generate_video_prompts');
        const defaultPromptContent = String(defaultPromptConfig?.content || '').trim();
        if (defaultPromptContent && currentStyleRaw.trim() === defaultPromptContent) {
            currentStyle = '';
        }
    } catch (error) {
        console.warn('Failed to load default Sora prompt config:', error);
    }

    const newStyle = await showTextareaModal(

        'Sora提示词设置',

        '生成Sora提示词的规则（留空使用默认）',

        currentStyle,

        '留空则使用默认提示词'

    );

    if (newStyle === null) {

        return;

    }

    if (newStyle === currentStyleRaw || (newStyle === '' && currentStyleRaw.trim() === '')) {

        return;

    }



    try {

        const response = await apiRequest(`/api/scripts/${APP_STATE.currentScript}`, {

            method: 'PUT',

            body: JSON.stringify({ sora_prompt_style: newStyle })

        });



        if (response.ok) {

            const updated = await response.json();

            APP_STATE.soraPromptStyle = updated.sora_prompt_style || '';

            APP_STATE.currentScriptInfo = updated;

            showToast('Sora提示词已更新', 'success');

        } else {

            const error = await response.json();

            showToast(error.detail || '保存失败', 'error');

        }

    } catch (error) {

        console.error('Failed to update sora prompt style:', error);

        showToast('保存失败', 'error');

    }

}



async function getPromptConfigByKey(promptKey) {

    const response = await apiRequest('/api/prompt-configs');

    if (!response || !response.ok) {

        throw new Error('获取提示词配置失败');

    }

    const configs = await response.json();

    if (!Array.isArray(configs)) {

        return null;

    }

    return configs.find(config => String(config?.key || '') === String(promptKey || '')) || null;

}



async function editPromptConfigByKey(promptKey, options = {}) {

    const {

        title = '提示词设置',

        label = '编辑提示词内容',

        placeholder = '',

        successMessage = '提示词已更新'

    } = options;



    try {

        const config = await getPromptConfigByKey(promptKey);

        if (!config || !config.id) {

            showToast('未找到对应提示词配置', 'error');

            return;

        }



        const currentContent = String(config.content || '');

        const nextContent = await showTextareaModal(

            title,

            label,

            currentContent,

            placeholder

        );

        if (nextContent === null || nextContent === currentContent) {

            return;

        }



        const updateResponse = await apiRequest(`/api/prompt-configs/${config.id}`, {

            method: 'PUT',

            body: JSON.stringify({ content: nextContent })

        });



        if (!updateResponse || !updateResponse.ok) {

            let errorMessage = '保存失败';

            try {

                const errorPayload = await updateResponse.json();

                errorMessage = errorPayload?.detail || errorMessage;

            } catch (error) {

                // ignore parse error

            }

            throw new Error(errorMessage);

        }



        showToast(successMessage, 'success');

    } catch (error) {

        console.error(`Failed to update prompt config ${promptKey}:`, error);

        showToast(`保存失败: ${error.message}`, 'error');

    }

}



async function editStoryboard2ImagePromptPrefix() {

    await editPromptConfigByKey('storyboard2_image_prompt_prefix', {

        title: '镜头图提示词设置',

        label: '故事板2生成镜头图时会自动前置该提示词',

        placeholder: '例如：生成动漫风格的图片',

        successMessage: '镜头图提示词已更新'

    });

}



// 渲染故事板右侧栏

function getStoryboardSidebarScrollTop() {

    const sidebarContent = document.querySelector('.storyboard-sidebar-content');

    if (sidebarContent) {

        return sidebarContent.scrollTop;

    }

    const sidebar = document.getElementById('storyboardSidebar');

    return sidebar ? sidebar.scrollTop : 0;

}



function restoreStoryboardSidebarScrollTop(scrollTop) {

    if (!Number.isFinite(scrollTop)) {

        return;

    }

    const sidebarContent = document.querySelector('.storyboard-sidebar-content');

    if (sidebarContent) {

        sidebarContent.scrollTop = scrollTop;

        return;

    }

    const sidebar = document.getElementById('storyboardSidebar');

    if (sidebar) {

        sidebar.scrollTop = scrollTop;

    }

}



function renderStoryboardSidebar() {

    const sidebar = document.getElementById('storyboardSidebar');

    if (!sidebar) {

        return;

    }

    const previousScrollTop = getStoryboardSidebarScrollTop();



    if (!APP_STATE.currentShot) {

        sidebar.innerHTML = '<div class="subject-sidebar-empty">请选择镜头</div>';

        return;

    }



    const shotLabel = getShotLabel(APP_STATE.currentShot);

    const isGenerating = APP_STATE.currentShot?.sora_prompt_status === 'generating';
    const isReasoningGenerating = APP_STATE.currentShot?.reasoning_prompt_status === 'generating';

    const cards = Array.isArray(APP_STATE.cards) ? APP_STATE.cards : [];

    let selectedCardIds = [];

    try {

        selectedCardIds = JSON.parse(APP_STATE.currentShot.selected_card_ids || '[]');

    } catch (error) {

        selectedCardIds = [];

    }

    const selectedSoundCardIds = getResolvedShotSoundCardIds(APP_STATE.currentShot);



    const videoStatus = APP_STATE.currentShot.video_status || 'idle';
    const episodeVideoSettings = getEpisodeStoryboardVideoSettings();
    const effectiveVideoSettings = getEffectiveShotStoryboardVideoSettings(APP_STATE.currentShot);
    const isModelOverrideEnabled = Boolean(effectiveVideoSettings.model_override_enabled);
    const isDurationOverrideEnabled = Boolean(effectiveVideoSettings.duration_override_enabled);
    const shotIdForSidebarRefresh = APP_STATE.currentShot.id;
    const globalVideoAccount = getEpisodeStoryboardVideoAppointAccount();
    const shotVideoAccount = getShotStoryboardVideoAppointAccount(APP_STATE.currentShot);
    const isShotAccountSelectable = effectiveVideoSettings.provider === 'moti';
    const shotAccountBlankLabel = `跟随全局账号（${globalVideoAccount || '不指定账号'}）`;
    const hasLoadedMotiVideoAccounts = Array.isArray(APP_STATE.motiVideoProviderAccounts?.records);
    if (isShotAccountSelectable && !hasLoadedMotiVideoAccounts) {
        loadMotiVideoProviderAccounts().then(() => {
            if (APP_STATE.currentShot && APP_STATE.currentShot.id === shotIdForSidebarRefresh) {
                renderStoryboardSidebar();
            }
        }).catch(error => {
            console.error('Failed to load Moti video accounts for shot sidebar:', error);
        });
    }
    const durationOptionsHtml = getStoryboardVideoModelConfig(effectiveVideoSettings.model).durations.map(item => {

        const selected = item === effectiveVideoSettings.duration ? ' selected' : '';

        return `<option value="${item}"${selected}>${item}秒</option>`;

    }).join('');
    const videoSettingsSectionHtml = `

        <div class="storyboard-prompt-section">

            <div class="storyboard-duration-toggle-row" style="margin-top:10px;">

                <span style="font-size:12px; color:#bbb;">单独设置模型</span>

                <label style="display:flex; align-items:center; cursor:pointer;">

                    <input id="shotModelOverrideCheckbox" type="checkbox" ${isModelOverrideEnabled ? 'checked' : ''} onchange="handleShotModelModeToggle(this.checked)" style="width:14px; height:14px; cursor:pointer;">

                </label>

            </div>

            <div style="display:flex; gap:8px; align-items:center; margin-top:8px;">

                <select id="shotModelSelect" class="form-input shot-image-size-select" style="flex:1;" onchange="handleShotModelOverrideChange(this.value)" ${isModelOverrideEnabled ? '' : 'disabled'}>

                    ${getStoryboardVideoModelOptionsHtml(effectiveVideoSettings.model)}

                </select>

            </div>

            <div id="shotModelHint" style="font-size:12px; color:#888; margin-top:6px;">

                ${isModelOverrideEnabled
                    ? `当前镜头单独使用 ${escapeHtml(effectiveVideoSettings.model)}`
                    : `当前跟随图/视频设置默认 ${escapeHtml(episodeVideoSettings.model)}`}

            </div>

            <div class="storyboard-duration-toggle-row">

                <span style="font-size:12px; color:#bbb;">单独设置时长</span>

                <label style="display:flex; align-items:center; cursor:pointer;">

                    <input id="shotDurationOverrideCheckbox" type="checkbox" ${isDurationOverrideEnabled ? 'checked' : ''} onchange="handleShotDurationModeToggle(this.checked)" style="width:14px; height:14px; cursor:pointer;">

                </label>

            </div>

            <div style="display:flex; gap:8px; align-items:center; margin-top:8px;">

                <select id="shotDurationSelect" class="form-input shot-image-size-select" style="flex:1;" onchange="handleShotDurationOverrideChange(this.value)" ${isDurationOverrideEnabled ? '' : 'disabled'}>

                    ${durationOptionsHtml}

                </select>

            </div>

            <div id="shotDurationHint" style="font-size:12px; color:#888; margin-top:6px;">

                ${isDurationOverrideEnabled
                    ? `当前镜头单独使用 ${effectiveVideoSettings.duration}s`
                    : `当前跟随图/视频设置默认 ${episodeVideoSettings.duration}s`}

            </div>

            <label style="margin-top:10px;">单独设置账号</label>

            <div style="display:flex; gap:8px; align-items:center; margin-top:8px;">

                <select id="shotVideoAppointAccountSelect" class="form-input shot-image-size-select" style="flex:1;" onchange="handleShotVideoAppointAccountChange(this.value)" ${isShotAccountSelectable ? '' : 'disabled'}>

                    ${buildMotiVideoAccountOptionsHtml(shotVideoAccount, { blankLabel: shotAccountBlankLabel })}

                </select>

            </div>

            <div id="shotVideoAppointAccountHint" style="font-size:12px; color:#888; margin-top:6px;">

                ${isShotAccountSelectable
                    ? (shotVideoAccount
                        ? `当前镜头单独使用账号 ${escapeHtml(shotVideoAccount)}`
                        : `当前跟随全局账号 ${escapeHtml(globalVideoAccount || '不指定账号')}`)
                    : '当前服务商无需指定账号'}

            </div>

        </div>

    `;



    // 如果sora_prompt_status为generating锛堟在生成中），显示生成涓彁绀?

    // 否则优先显示用户保存的sora_prompt锛屽果没有则使用storyboard_video_prompt

    let soraPromptText;

    if (APP_STATE.currentShot.sora_prompt_status === 'generating') {

        soraPromptText = '生成中，请稍候...';

    } else {

        // 鉁?优先使用用户保存鐨?sora_prompt锛屽果没有则使用鑷姩生成鐨?storyboard_video_prompt

        // 鉁?鏈€后回閫€鍒?buildSoraPromptTableOnly（仅表格，不鍚棰戦格）

        soraPromptText = (APP_STATE.currentShot.sora_prompt || '').trim()

            || (APP_STATE.currentShot.storyboard_video_prompt || '').trim()

            || buildSoraPromptTableOnly(APP_STATE.currentShot);

    }



    const selectedCharacters = cards.filter(card => (

        selectedCardIds.includes(card.id) && card.card_type === '角色'

    ));

    const castHtml = selectedCharacters.length > 0

        ? selectedCharacters.map(card => `

            <span class="storyboard-cast-chip" onclick="toggleShotSubject(${card.id})">

                ${escapeHtml(card.name)}

            </span>

        `).join('')

        : '<div class="storyboard-cast-empty">鏈€夋嫨</div>';



    const taskId = getShotDisplayTaskId(APP_STATE.currentShot);

    const currentShotStatusMeta = getShotVideoStatusInteractionMeta(APP_STATE.currentShot, { stopPropagation: false });



    // 鐘舵€佷俊鎭紙显示在标题右侧）

    let statusBadgeHtml = '';

    if (videoStatus === 'submitting') {

        statusBadgeHtml = '<span class="video-status-badge processing">提交中</span>';

    } else if (videoStatus === 'preparing') {

        statusBadgeHtml = '<span class="video-status-badge processing">准备中</span>';

    } else if (videoStatus === 'processing') {

        statusBadgeHtml = `<span class="video-status-badge processing" ${currentShotStatusMeta.inlineAttrs}>生成中</span>`;

    } else if (videoStatus === 'completed') {

        statusBadgeHtml = '<span class="video-status-badge completed">已完成</span>';

    } else if (videoStatus === 'failed') {

        statusBadgeHtml = `<span class="video-status-badge failed" ${currentShotStatusMeta.inlineAttrs}>失败</span>`;

    }



    // task_id显示（在鏍囬右侧锛?

    const taskIdHtml = taskId ? `<span class="task-id-badge">ID: ${escapeHtml(taskId)}</span>` : '';



    // 判断鏄惁已生成Sora提示词（或已经生成过瑙嗛锛岃明一定有提示词）

    const hasStoredPrompt = Boolean(
        String(APP_STATE.currentShot.sora_prompt || '').trim()
        || String(APP_STATE.currentShot.storyboard_video_prompt || '').trim()
    );
    const hasSoraPrompt = APP_STATE.currentShot.sora_prompt_status === 'completed'

        || hasStoredPrompt

        || ['submitting', 'preparing', 'processing', 'completed', 'failed'].includes(videoStatus);

    const selectedSceneCardIds = getSelectedStoryboardSceneCardIds(APP_STATE.currentShot);
    const uploadedSceneImageUrl = String(APP_STATE.currentShot.uploaded_scene_image_url || '').trim();
    const useUploadedSceneImage = Boolean(APP_STATE.currentShot.use_uploaded_scene_image && uploadedSceneImageUrl);
    const sceneUploadControlsHtml = `

        <div style="display: flex; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: wrap;">

            <button class="secondary-button storyboard-tool-button" onclick="uploadStoryboardShotSceneImage()">上传图片</button>

            <span style="font-size: 12px; color: #888;">${uploadedSceneImageUrl ? '已上传镜头场景图，可在下方场景卡中多选切换' : '上传镜头专属场景图，不影响其他镜头和场景卡'}</span>

        </div>

    `;
    const subjectSelectionSectionHtml = hasSoraPrompt ? (() => {

        const { characters, scenes, props, sounds } = groupSubjectCardsByType(cards);

        const renderCards = (cardList) => cardList.map(card => {
            const previewImage = getCardPreviewImage(card);
            const previewHtml = previewImage
                ? `<img class="storyboard-subject-image" src="${getImageUrl(previewImage)}" alt="${escapeHtml(card.name)}">`
                : '<div class="storyboard-subject-placeholder">NO IMAGE</div>';
            return `

                <div class="storyboard-subject-card ${selectedCardIds.includes(card.id) ? 'selected' : ''}"

                     onclick="toggleShotSubject(${card.id})">

                    <div class="storyboard-subject-thumb">${previewHtml}</div>

                    <div class="storyboard-subject-info">

                        <div class="storyboard-subject-name">${escapeHtml(card.name)}</div>

                        <div class="storyboard-subject-type">${escapeHtml(card.card_type)}</div>

                    </div>

                </div>

            `;
        }).join('');

        const renderSceneCards = () => {
            const cardsHtml = scenes.map(card => {
                const previewImage = getCardPreviewImage(card);
                const previewHtml = previewImage
                    ? `<img class="storyboard-subject-image" src="${getImageUrl(previewImage)}" alt="${escapeHtml(card.name)}">`
                    : '<div class="storyboard-subject-placeholder">NO IMAGE</div>';
                const isSelected = !useUploadedSceneImage && selectedSceneCardIds.includes(card.id);
                return `

                    <div class="storyboard-subject-card ${isSelected ? 'selected' : ''}"

                         onclick="toggleShotSubject(${card.id})">

                        <div class="storyboard-subject-thumb">${previewHtml}</div>

                        <div class="storyboard-subject-info">

                            <div class="storyboard-subject-name">${escapeHtml(card.name)}</div>

                            <div class="storyboard-subject-type">${escapeHtml(card.card_type)}</div>

                        </div>

                    </div>

                `;
            }).join('');

            const uploadedCardHtml = uploadedSceneImageUrl ? `

                <div class="storyboard-subject-card ${useUploadedSceneImage ? 'selected' : ''}"

                     onclick="toggleUploadedShotSceneImageSelection()">

                    <div class="storyboard-subject-thumb">

                        <img class="storyboard-subject-image" src="${getImageUrl(uploadedSceneImageUrl)}" alt="镜头场景图">

                    </div>

                    <div class="storyboard-subject-info">

                        <div class="storyboard-subject-name">镜头上传图</div>

                        <div class="storyboard-subject-type">场景 · 当前镜头</div>

                    </div>

                </div>

            ` : '';

            return `${cardsHtml}${uploadedCardHtml}`;
        };

        let html = '';

        if (characters.length > 0) {
            html += `

                <div class="subject-type-group">

                    <div class="subject-type-label">角色</div>

                    <div class="storyboard-subject-grid">${renderCards(characters)}</div>

                </div>

            `;
        }

        if (scenes.length > 0 || uploadedSceneImageUrl) {
            html += `

                <div class="subject-type-group">

                    <div class="subject-type-label">场景</div>

                    <div class="storyboard-subject-grid">${renderSceneCards()}</div>

                </div>

            `;
        }

        if (props.length > 0) {
            html += `

                <div class="subject-type-group">

                    <div class="subject-type-label">道具</div>

                    <div class="storyboard-subject-grid">${renderCards(props)}</div>

                </div>

            `;
        }

        if (sounds.length > 0) {
            const renderSoundCards = (cardList) => cardList.map(card => {
                const boundRoleCard = findBoundRoleCardForSound(card);
                const previewImage = boundRoleCard
                    ? getCardPreviewImage(boundRoleCard)
                    : getCardPreviewImage(card);
                const previewHtml = previewImage
                    ? `<img class="storyboard-subject-image" src="${getImageUrl(previewImage)}" alt="${escapeHtml(card.name)}">`
                    : '<div class="storyboard-subject-placeholder">NO IMAGE</div>';
                const referenceAudio = getCardReferenceAudio(card);
                const audioCount = Array.isArray(card.audios) ? card.audios.length : 0;
                const durationLabel = formatAudioDurationLabel(referenceAudio?.duration_seconds);
                const metaParts = [];

                if (String(card.name || '').trim() === '旁白') {
                    metaParts.push('旁白');
                } else if (boundRoleCard && boundRoleCard.name) {
                    metaParts.push(`绑定:${boundRoleCard.name}`);
                }

                if (audioCount > 0) {
                    metaParts.push(`音频${audioCount}条`);
                }

                if (durationLabel) {
                    metaParts.push(durationLabel);
                }

                const typeLabel = metaParts.length > 0
                    ? `声音 · ${escapeHtml(metaParts.join(' / '))}`
                    : '声音';

                return `

                    <div class="storyboard-subject-card ${selectedSoundCardIds.includes(card.id) ? 'selected' : ''}"

                         onclick="toggleShotSoundCard(${card.id})">

                        <div class="storyboard-subject-thumb">${previewHtml}</div>

                        <div class="storyboard-subject-info">

                            <div class="storyboard-subject-name">${escapeHtml(card.name)}</div>

                            <div class="storyboard-subject-type">${typeLabel}</div>

                        </div>

                    </div>

                `;
            }).join('');

            html += `

                <div class="subject-type-group">

                    <div class="subject-type-label">声音</div>

                    <div class="storyboard-subject-grid">${renderSoundCards(sounds)}</div>

                </div>

            `;
        }

        return `

            <div class="storyboard-cards-section">

                <label>选择主体</label>

                ${html || '<div class="storyboard-empty-state">暂无主体卡片</div>'}

            </div>

        `;
    })() : '';



    sidebar.innerHTML = `

        <div class="storyboard-sidebar-content">

            <div class="storyboard-sidebar-title" style="display: flex; justify-content: space-between; align-items: center; font-size: 16px; font-weight: 500; color: #fff; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid #2a2a2a;">

                <span>${escapeHtml(shotLabel)}</span>

                <div style="display: flex; gap: 8px; align-items: center; font-size: 12px;">

                    ${statusBadgeHtml}

                    ${taskIdHtml}

                </div>

            </div>



            <div class="storyboard-sora-actions" style="margin-bottom: 10px;">
                <div class="storyboard-sora-actions-left">
                    <div class="storyboard-large-shot-menu" id="largeShotPromptMenu" onmouseenter="cancelHideLargeShotTemplateMenu()" onmouseleave="scheduleHideLargeShotTemplateMenu()">
                        <button class="secondary-button storyboard-tool-button" id="generateLargeShotPromptBtn" onclick="toggleLargeShotTemplateMenu(event)" ${isGenerating ? 'disabled' : ''}>生成大镜头提示词</button>
                        <div class="storyboard-large-shot-dropdown" id="largeShotPromptDropdown">
                            ${renderLargeShotTemplateMenu(isGenerating)}
                        </div>
                    </div>
                </div>
                <div class="storyboard-sora-actions-right">
                    <button class="primary-button storyboard-tool-button" onclick="copySoraPrompt()">复制提示词</button>
                    <button class="primary-button storyboard-tool-button" id="generateReasoningPromptBtn" onclick="generateStoryboardReasoningPrompt()" ${isReasoningGenerating ? 'disabled' : ''}>${isReasoningGenerating ? '推理中...' : '生成推理提示词'}</button>
                    <button class="primary-button storyboard-tool-button" id="generateSoraPromptBtn" onclick="generateSoraPrompt()" ${isGenerating ? 'disabled' : ''}>生成Sora提示词</button>
                </div>
            </div>



            <div class="storyboard-prompt-section">

                <label>原剧本段落（失去焦点时自动保存）</label>

                <textarea id="scriptExcerpt" class="script-excerpt-textarea" rows="4" placeholder="原剧本段落（作为生成Sora提示词的上下文）">${escapeHtml(APP_STATE.currentShot.script_excerpt || '')}</textarea>

            </div>



            ${!hasSoraPrompt ? `

                <div class="storyboard-prompt-section">

                    <label>场景描述（失去焦点时自动保存）</label>

                    <textarea id="sceneOverride" class="scene-override-textarea" rows="6" placeholder="场景描述（可手动编辑或从主体卡片自动提取）">${escapeHtml((APP_STATE.currentShot.scene_override || '').trim())}</textarea>

                    ${sceneUploadControlsHtml}

                </div>



                <div class="storyboard-prompt-section">

                    <label>Sora提示词</label>

                    <textarea id="soraPrompt" class="sora-prompt-textarea" rows="10" placeholder="点击上方按钮生成" style="color: ${APP_STATE.currentShot.sora_prompt_status === 'generating' ? '#888' : ''}" disabled>${escapeHtml(soraPromptText)}</textarea>

                </div>

                ${videoSettingsSectionHtml}

            ` : `

                <div class="storyboard-prompt-section">

                    <label>场景描述（失去焦点时自动保存）</label>

                    <textarea id="sceneOverride" class="scene-override-textarea" rows="6" placeholder="场景描述（可手动编辑或从主体卡片自动提取）">${escapeHtml((APP_STATE.currentShot.scene_override || '').trim())}</textarea>

                    ${sceneUploadControlsHtml}

                </div>



                <div class="storyboard-prompt-section">

                    <label>Sora提示词（失去焦点时自动保存）</label>

                    <textarea id="soraPrompt" class="sora-prompt-textarea" rows="10" placeholder="Sora提示词">${escapeHtml(soraPromptText)}</textarea>

                </div>

                ${videoSettingsSectionHtml}



                ${subjectSelectionSectionHtml}



                <div class="storyboard-collages-section">

                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">

                        <label>首帧参考图</label>

                        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                            <button class="secondary-button storyboard-tool-button" type="button" onclick="uploadFirstFrameReferenceImage()">上传图片</button>
                            <div style="font-size: 11px; color: #888;">单选，可取消；上传后不会自动选中</div>
                        </div>

                    </div>

                    <div class="storyboard-collage-grid" id="shotFirstFrameGrid">

                        <div class="loading" style="padding: 10px; font-size: 12px;">加载中...</div>

                    </div>

                </div>

                <div class="storyboard-sidebar-actions" style="margin-top: 16px;"></div>

            `}

        </div>

    `;



    // 直接渲染首帧参考图候选（仅在已生成Sora提示词后）

    if (hasSoraPrompt) {

        renderShotFirstFrameReferenceGrid();

    }



    // 鍒濆化模鏉块览（仅在已生成Sora提示词后锛?

    if (hasSoraPrompt) {

        updateTemplatePreview();

    }



    requestAnimationFrame(() => {

        restoreStoryboardSidebarScrollTop(previousScrollTop);

    });



    // 鉁?为字段添加失去焦点时鑷姩保存功能

    setupAutoSaveOnBlur();
    updateVideoGenerationButton();

}



// 设置失去焦点时自动保瀛?

function setupAutoSaveOnBlur() {

    // 原剧鏈钀?

    const scriptExcerptTextarea = document.getElementById('scriptExcerpt');

    if (scriptExcerptTextarea) {

        scriptExcerptTextarea.addEventListener('blur', async function() {

            const newValue = this.value.trim();

            const oldValue = (APP_STATE.currentShot.script_excerpt || '').trim();

            if (newValue !== oldValue) {

                await autoSaveShotField('script_excerpt', this.value);

            }

        });

    }



    // 场景描述

    const sceneOverrideTextarea = document.getElementById('sceneOverride');

    if (sceneOverrideTextarea) {

        sceneOverrideTextarea.addEventListener('blur', async function() {

            const newValue = this.value.trim();

            const oldValue = (APP_STATE.currentShot.scene_override || '').trim();

            if (newValue !== oldValue) {

                await autoSaveShotField('scene_override', this.value);

            }

        });

    }



    // Sora提示璇?

    const soraPromptTextarea = document.getElementById('soraPrompt');

    if (soraPromptTextarea) {

        soraPromptTextarea.addEventListener('blur', async function() {

            const newValue = this.value.trim();

            const oldValue = (APP_STATE.currentShot.sora_prompt || '').trim();

            if (newValue !== oldValue) {

                await autoSaveShotField('sora_prompt', this.value);

            }

        });

    }

    refreshLargeShotTemplateMenu();

}



// 鑷姩保存镜头瀛楁

async function autoSaveShotField(fieldName, value) {

    if (!APP_STATE.currentShot) return;



    const shotId = APP_STATE.currentShot.id;

    const requestBody = {};

    requestBody[fieldName] = value;



    try {

        const response = await apiRequest(`/api/shots/${shotId}`, {

            method: 'PUT',

            headers: { 'Content-Type': 'application/json' },

            body: JSON.stringify(requestBody)

        });



        if (response && response.ok) {

            const updatedShot = await response.json();

            // 更新鏈湴鐘舵€?

            APP_STATE.currentShot[fieldName] = value;

            // 鍚屾更新APP_STATE.shots涓殑数据

            const shotIndex = APP_STATE.shots.findIndex(s => s.id === shotId);

            if (shotIndex !== -1) {

                APP_STATE.shots[shotIndex][fieldName] = value;

            }



            // 根据瀛楁显示不同的提绀?

            let fieldDisplayName = fieldName;

            if (fieldName === 'script_excerpt') {

                fieldDisplayName = '原剧本段落';

            } else if (fieldName === 'scene_override') {

                fieldDisplayName = '场景描述';

            } else if (fieldName === 'sora_prompt') {

                fieldDisplayName = 'Sora提示词';

            }



            showToast(`${fieldDisplayName}已自动保存`, 'success');

        } else {

            const error = await response.json();

            showToast(`保存失败: ${error.detail || '未知错误'}`, 'error');

        }

    } catch (error) {

        console.error(`Failed to save ${fieldName}:`, error);

        showToast(`保存失败`, 'error');

    }

}



// 增量更新Sora提示词textarea（不重建整个sidebar锛?

function updateSoraPromptTextarea() {

    const textarea = document.getElementById('soraPrompt');

    if (!textarea || !APP_STATE.currentShot) return;



    // 如果sora_prompt_status为generating锛堟在生成中），涓嶈更新

    if (APP_STATE.currentShot.sora_prompt_status === 'generating') {

        // 如果textarea还不鏄?生成涓?鐘舵€侊紝更新为生成中

        if (!textarea.value.includes('生成中，请稍候')) {

            textarea.value = '生成中，请稍候...';

            textarea.style.color = '#888';

        }

        return;

    }



    // 有新鍐呭了，构建显示文本

    // 鉁?优先使用用户保存鐨?sora_prompt锛屽果没有则使用鑷姩生成鐨?storyboard_video_prompt

    // 鉁?鏈€后回閫€鍒?buildSoraPromptTableOnly（仅表格，不鍚棰戦格）

    const soraPromptText = (APP_STATE.currentShot.sora_prompt || '').trim()

        || (APP_STATE.currentShot.storyboard_video_prompt || '').trim()

        || buildSoraPromptTableOnly(APP_STATE.currentShot);



    // 鍙湪用户鏈幏得焦点时更新（避免中鏂緭入）

    if (document.activeElement !== textarea) {

        if (textarea.value !== soraPromptText) {

            textarea.value = soraPromptText;

            // 鎭㈠正常颜色锛堝果之前是灰色锛?

            textarea.style.color = '';

        }

    }

}



async function saveShotDurationOverride(duration, durationOverrideEnabled) {

    if (!APP_STATE.currentShot) return;

    const episodeSettings = getEpisodeStoryboardVideoSettings();
    const normalizedDuration = normalizeStoryboardVideoDuration(
        duration,
        episodeSettings.model,
        episodeSettings.duration
    );
    const shotId = APP_STATE.currentShot.id;

    try {

        const response = await apiRequest(`/api/shots/${shotId}`, {

            method: 'PUT',

            headers: { 'Content-Type': 'application/json' },

            body: JSON.stringify({

                duration: normalizedDuration,

                duration_override_enabled: Boolean(durationOverrideEnabled)

            })

        });



        if (response && response.ok) {

            const updatedShot = await response.json();

            updateShotInState(shotId, updatedShot);
            syncEpisodeStoryboardVideoSettingsToShotState();
            refreshShotDurationControls();

            showToast(
                Boolean(durationOverrideEnabled)
                    ? `镜头时长已设置为 ${normalizedDuration}s`
                    : `镜头时长已恢复跟随图/视频设置（${episodeSettings.duration}s）`,
                'success'
            );

        } else if (response) {

            const error = await response.json();
            throw new Error(error.detail || '镜头时长保存失败');

        }

    } catch (error) {

        console.error('Failed to save shot duration override:', error);
        showToast(error.message || '镜头时长保存失败', 'error');
        refreshShotDurationControls();

    }

}


async function saveShotStoryboardVideoModelOverride(model, modelOverrideEnabled) {

    if (!APP_STATE.currentShot) return;

    const episodeSettings = getEpisodeStoryboardVideoSettings();
    const normalizedModel = normalizeStoryboardVideoModel(
        model,
        episodeSettings.model
    );
    const shotId = APP_STATE.currentShot.id;

    try {

        const response = await apiRequest(`/api/shots/${shotId}`, {

            method: 'PUT',

            headers: { 'Content-Type': 'application/json' },

            body: JSON.stringify({

                storyboard_video_model: normalizedModel,

                storyboard_video_model_override_enabled: Boolean(modelOverrideEnabled)

            })

        });



        if (response && response.ok) {

            const updatedShot = await response.json();

            updateShotInState(shotId, updatedShot);
            syncEpisodeStoryboardVideoSettingsToShotState();
            refreshShotDurationControls();

            showToast(
                Boolean(modelOverrideEnabled)
                    ? `镜头模型已设置为 ${normalizedModel}`
                    : `镜头模型已恢复跟随图/视频设置（${episodeSettings.model}）`,
                'success'
            );

        } else if (response) {

            const error = await response.json();
            throw new Error(error.detail || '镜头模型保存失败');

        }

    } catch (error) {

        console.error('Failed to save shot storyboard video model override:', error);
        showToast(error.message || '镜头模型保存失败', 'error');
        refreshShotDurationControls();

    }

}


async function saveShotStoryboardVideoAppointAccountOverride(appointAccount) {

    if (!APP_STATE.currentShot) return;

    const shotId = APP_STATE.currentShot.id;
    const normalizedAccount = normalizeMotiVideoAccountName(appointAccount);

    try {

        const response = await apiRequest(`/api/shots/${shotId}`, {

            method: 'PUT',

            headers: { 'Content-Type': 'application/json' },

            body: JSON.stringify({

                storyboard_video_appoint_account: normalizedAccount

            })

        });

        if (response && response.ok) {

            const updatedShot = await response.json();

            updateShotInState(shotId, updatedShot);
            syncEpisodeStoryboardVideoSettingsToShotState();
            refreshShotDurationControls();

            showToast(
                normalizedAccount
                    ? `镜头账号已设置为 ${normalizedAccount}`
                    : `镜头账号已恢复跟随全局设置`,
                'success'
            );

        } else if (response) {

            const error = await response.json();
            throw new Error(error.detail || '镜头账号保存失败');

        }

    } catch (error) {

        console.error('Failed to save shot storyboard video appoint account:', error);
        showToast(error.message || '镜头账号保存失败', 'error');
        refreshShotDurationControls();

    }

}


async function handleShotModelOverrideChange(value) {

    if (!APP_STATE.currentShot) return;

    await saveShotStoryboardVideoModelOverride(value, true);

}


async function handleShotModelModeToggle(enabled) {

    if (!APP_STATE.currentShot) return;

    if (enabled) {

        const modelSelect = document.getElementById('shotModelSelect');
        const selectedModel = modelSelect ? modelSelect.value : getEpisodeStoryboardVideoSettings().model;
        await saveShotStoryboardVideoModelOverride(selectedModel, true);
        return;

    }

    const episodeSettings = getEpisodeStoryboardVideoSettings();
    await saveShotStoryboardVideoModelOverride(episodeSettings.model, false);

}


async function handleShotVideoAppointAccountChange(value) {

    if (!APP_STATE.currentShot) return;

    await saveShotStoryboardVideoAppointAccountOverride(value);

}


async function handleShotDurationOverrideChange(value) {

    if (!APP_STATE.currentShot) return;

    await saveShotDurationOverride(value, true);

}


async function handleShotDurationModeToggle(enabled) {

    if (!APP_STATE.currentShot) return;

    if (enabled) {

        const durationSelect = document.getElementById('shotDurationSelect');
        const selectedDuration = durationSelect ? durationSelect.value : getEpisodeStoryboardVideoSettings().duration;
        await saveShotDurationOverride(selectedDuration, true);
        return;

    }

    const episodeSettings = getEpisodeStoryboardVideoSettings();
    await saveShotDurationOverride(episodeSettings.duration, false);

}



// 增量更新瑙嗛生成按钮鐘舵€侊紙不重建整个sidebar锛?

function updateVideoGenerationButton() {

    const actionsDiv = document.querySelector('.storyboard-sidebar-actions');

    if (!actionsDiv || !APP_STATE.currentShot) return;



    const videoStatus = APP_STATE.currentShot.video_status || 'idle';

    let generateButtonHtml = '';
    const copyButtonHtml = `<button class=\"secondary-button\" style=\"width: 100%;\" onclick=\"duplicateCurrentShotForVideo()\">复制镜头</button>`;



    if (videoStatus === 'submitting') {

        generateButtonHtml = `

            ${copyButtonHtml}

            <button class=\"primary-button\" style=\"width: 100%; margin-top: 10px;\" disabled>提交中...</button>

        `;

    } else if (videoStatus === 'preparing') {

        generateButtonHtml = `

            ${copyButtonHtml}

            <button class=\"primary-button\" style=\"width: 100%; margin-top: 10px;\" disabled>准备中...</button>

        `;

    } else if (videoStatus === 'processing') {

        generateButtonHtml = `

            ${copyButtonHtml}

            <button class=\"primary-button\" style=\"width: 100%; margin-top: 10px;\" disabled>生成中...</button>

        `;

    } else if (videoStatus === 'completed') {

        generateButtonHtml = `

            <button class=\"secondary-button\" style=\"width: 100%;\" onclick=\"exportVideo(${APP_STATE.currentShot.id})\">导出视频</button>

            <div style=\"display:flex; gap:10px; margin-top: 10px;\">
                <button class=\"secondary-button\" style=\"flex: 1;\" onclick=\"duplicateCurrentShotForVideo()\">复制镜头</button>
                <button class=\"primary-button\" style=\"flex: 1;\" onclick=\"generateVideo()\">重新生成</button>
            </div>

        `;

    } else {

        generateButtonHtml = `

            <div style=\"display:flex; gap:10px;\">
                <button class=\"secondary-button\" style=\"flex: 1;\" onclick=\"duplicateCurrentShotForVideo()\">复制镜头</button>
                <button class=\"primary-button\" style=\"flex: 1;\" onclick=\"generateVideo()\">生成视频</button>
            </div>

        `;

    }

    actionsDiv.innerHTML = generateButtonHtml;

}



// 更新瑙嗛鐘舵€佹樉绀?

function updateVideoStatusDisplay() {

    const statusDisplay = document.getElementById('videoStatusDisplay');

    if (!statusDisplay || !APP_STATE.currentShot) return;



    const videoStatus = APP_STATE.currentShot.video_status || 'idle';

    const taskId = getShotDisplayTaskId(APP_STATE.currentShot);

    const interactionMeta = getShotVideoStatusInteractionMeta(APP_STATE.currentShot, { stopPropagation: false });



    let statusHtml = '';

    if (videoStatus === 'submitting') {

        statusHtml = '<div class="video-status processing">提交任务中...</div>';

    } else if (videoStatus === 'preparing') {

        statusHtml = '<div class="video-status processing">准备中...</div>';

    } else if (videoStatus === 'processing') {

        statusHtml = `

            <div class="video-status processing" ${interactionMeta.inlineAttrs}>

                <div>视频生成中...</div>

                ${taskId ? `<div style="font-size: 11px; color: #888; margin-top: 5px;">任务ID: ${escapeHtml(taskId)}</div>` : ''}

            </div>

        `;

    } else if (videoStatus === 'completed') {

        statusHtml = `

            <div class="video-status completed">

                <div>视频生成成功</div>

                ${taskId ? `<div style="font-size: 11px; color: #888; margin-top: 5px;">任务ID: ${escapeHtml(taskId)}</div>` : ''}

            </div>

        `;

    } else if (videoStatus === 'failed') {

        statusHtml = `

            <div class="video-status failed" ${interactionMeta.inlineAttrs}>

                <div>视频生成失败</div>

                ${taskId ? `<div style="font-size: 11px; color: #888; margin-top: 5px;">任务ID: ${escapeHtml(taskId)}</div>` : ''}

            </div>

        `;

    }



    statusDisplay.innerHTML = statusHtml;

}



// 增量更新右侧栏标题中的状态badge和task_id

function updateSidebarTitleStatus() {

    if (!APP_STATE.currentShot) return;



    const shotLabel = getShotLabel(APP_STATE.currentShot);

    const videoStatus = APP_STATE.currentShot.video_status || 'idle';

    const taskId = getShotDisplayTaskId(APP_STATE.currentShot);

    const interactionMeta = getShotVideoStatusInteractionMeta(APP_STATE.currentShot, { stopPropagation: false });



    // 鐘舵€乥adge

    let statusBadgeHtml = '';

    if (videoStatus === 'submitting') {

        statusBadgeHtml = '<span class="video-status-badge processing">提交中</span>';

    } else if (videoStatus === 'preparing') {

        statusBadgeHtml = '<span class="video-status-badge processing">准备中</span>';

    } else if (videoStatus === 'processing') {

        statusBadgeHtml = `<span class="video-status-badge processing" ${interactionMeta.inlineAttrs}>生成中</span>`;

    } else if (videoStatus === 'completed') {

        statusBadgeHtml = '<span class="video-status-badge completed">已完成</span>';

    } else if (videoStatus === 'failed') {

        statusBadgeHtml = `<span class="video-status-badge failed" ${interactionMeta.inlineAttrs}>失败</span>`;

    }



    // task_id显示

    const taskIdHtml = taskId ? `<span class="task-id-badge">ID: ${escapeHtml(taskId)}</span>` : '';



    // 更新鏍囬DOM

    const titleElement = document.querySelector('.storyboard-sidebar-title');

    if (titleElement) {

        titleElement.innerHTML = `

            <span>${escapeHtml(shotLabel)}</span>

            <div style="display: flex; gap: 8px; align-items: center; font-size: 12px;">

                ${statusBadgeHtml}

                ${taskIdHtml}

            </div>

        `;

    }

}



// 更新模板棰勮

function updateTemplatePreview() {

    const select = document.getElementById('promptTemplate');

    const preview = document.getElementById('templatePreview');



    if (!select || !preview) return;



    const selectedName = select.value;

    const template = APP_STATE.templates.find(t => t.name === selectedName);



    if (template) {

        preview.value = template.content || '';

    } else {

        preview.value = '';

    }

}



// 加载模板

function loadTemplate() {

    const select = document.getElementById('templateSelect');

    const templateId = parseInt(select.value);



    if (!templateId) return;



    const template = APP_STATE.templates.find(t => t.id === templateId);

    if (template) {

        document.getElementById('promptTemplate').value = template.content;

    }

}



// 保存为模鏉?

async function saveAsTemplate() {

    const content = document.getElementById('templatePreview').value;

    if (!content.trim()) {

        showToast('请先输入提示词内容', 'info');

        return;

    }



    const name = await showInputModal('保存为模板', '请输入模板名称', '', '例如：2D动画风格');

    if (!name) return;



    try {

        const response = await apiRequest('/api/templates', {

            method: 'POST',

            body: JSON.stringify({ name: name.trim(), content: content })

        });



        if (response.ok) {

            const templatesResponse = await apiRequest('/api/templates');

            APP_STATE.templates = await templatesResponse.json();

            showToast('保存成功', 'success');

            renderStoryboardSidebar();

        } else {

            showToast('保存失败', 'error');

        }

    } catch (error) {

        console.error('Failed to save template:', error);

        showToast('保存失败', 'error');

    }

}



// 切换卡片选择

function toggleCardSelection(cardId) {

    const checkbox = document.getElementById(`card-${cardId}`);

    checkbox.checked = !checkbox.checked;

}



function getSelectedShotCardIds() {

    if (!APP_STATE.currentShot) return [];

    try {

        return JSON.parse(APP_STATE.currentShot.selected_card_ids || '[]');

    } catch (error) {

        return [];

    }

}


async function duplicateCurrentShotForVideo() {

    const shot = APP_STATE.currentShot;

    if (!shot || !shot.id) {

        showToast('当前没有可复制的镜头', 'error');

        return;

    }

    try {

        const duplicateResponse = await apiRequest(`/api/shots/${shot.id}/duplicate`, {

            method: 'POST',

            body: JSON.stringify({})

        });

        if (!duplicateResponse.ok) {

            const error = await duplicateResponse.json();

            if (shouldShowStoryboardVideoWaitDialog(error.detail)) {
                showAlertDialog(error.detail);
            } else {
                showAlertDialog(error.detail || '复制镜头失败');
            }

            return;

        }

        const newShot = await duplicateResponse.json();
        const clonePayload = buildShotCloneSyncPayload(shot);
        const syncResponse = await apiRequest(`/api/shots/${newShot.id}`, {
            method: 'PUT',
            body: JSON.stringify(clonePayload)
        });

        if (!syncResponse || !syncResponse.ok) {
            const error = syncResponse ? await syncResponse.json() : null;
            throw new Error(error?.detail || '复制镜头后的同步失败');
        }

        const syncedShot = await syncResponse.json();
        updateShotInState(newShot.id, syncedShot);

        const shotsResponse = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/shots`);

        if (shotsResponse && shotsResponse.ok) {
            APP_STATE.shots = await shotsResponse.json();
        }

        const refreshedShot = APP_STATE.shots.find(s => s.id === newShot.id) || syncedShot;
        await selectShot(refreshedShot.id);
        showToast(`已复制为镜头 ${getShotLabel(refreshedShot)}`, 'success');

    } catch (error) {

        console.error('Failed to duplicate shot:', error);
        showAlertDialog('复制镜头失败');

    }

}



function getSelectedStoryboardSceneCardIds(shot = APP_STATE.currentShot) {

    if (!shot) return [];

    const selectedIds = (() => {
        try {
            return JSON.parse(shot.selected_card_ids || '[]');
        } catch (error) {
            return [];
        }
    })();

    const sceneIdSet = new Set(
        (Array.isArray(APP_STATE.cards) ? APP_STATE.cards : [])
            .filter(card => card.card_type === '场景')
            .map(card => card.id)
    );

    const selectedSceneIds = [];
    for (const cardId of selectedIds) {
        if (sceneIdSet.has(cardId)) {
            selectedSceneIds.push(cardId);
        }
    }

    return selectedSceneIds;

}



function getSelectedStoryboardSceneCardId(shot = APP_STATE.currentShot) {

    const selectedSceneIds = getSelectedStoryboardSceneCardIds(shot);
    return selectedSceneIds.length > 0 ? selectedSceneIds[0] : null;

}



function applyCurrentShotSceneImageState(payload = {}) {

    if (!APP_STATE.currentShot) return;

    APP_STATE.currentShot.uploaded_scene_image_url = payload?.uploaded_scene_image_url || '';
    APP_STATE.currentShot.use_uploaded_scene_image = Boolean(payload?.use_uploaded_scene_image);
    APP_STATE.currentShot.selected_scene_image_url = payload?.selected_scene_image_url || '';

    const shotIndex = APP_STATE.shots.findIndex(shot => shot.id === APP_STATE.currentShot.id);
    if (shotIndex !== -1) {
        APP_STATE.shots[shotIndex].uploaded_scene_image_url = APP_STATE.currentShot.uploaded_scene_image_url;
        APP_STATE.shots[shotIndex].use_uploaded_scene_image = APP_STATE.currentShot.use_uploaded_scene_image;
        APP_STATE.shots[shotIndex].selected_scene_image_url = APP_STATE.currentShot.selected_scene_image_url;
    }

}



function parseSelectedShotSoundCardIds(rawValue) {

    if (rawValue === null || rawValue === undefined) {

        return null;

    }



    let parsed = rawValue;

    if (typeof rawValue === 'string') {

        if (!rawValue.trim()) {

            return null;

        }

        try {

            parsed = JSON.parse(rawValue);

        } catch (error) {

            return null;

        }

    }



    if (!Array.isArray(parsed)) {

        return null;

    }



    const normalized = [];

    const seen = new Set();

    parsed.forEach(item => {

        const cardId = Number(item);

        if (!Number.isInteger(cardId) || cardId <= 0 || seen.has(cardId)) {

            return;

        }

        seen.add(cardId);

        normalized.push(cardId);

    });

    return normalized;

}



function getStoryboardSoundCards() {

    return Array.isArray(APP_STATE.cards)

        ? APP_STATE.cards.filter(card => card.card_type === '声音')

        : [];

}



function getDefaultShotSoundCardIds(shot = APP_STATE.currentShot) {

    if (!shot) return [];



    const selectedCardIds = (() => {

        try {

            return JSON.parse(shot.selected_card_ids || '[]');

        } catch (error) {

            return [];

        }

    })();

    const allCards = Array.isArray(APP_STATE.cards) ? APP_STATE.cards : [];

    const selectedRoles = selectedCardIds

        .map(cardId => allCards.find(card => card.id === cardId && card.card_type === '角色'))

        .filter(Boolean);

    const soundCards = getStoryboardSoundCards();

    const resolved = [];

    const seen = new Set();



    selectedRoles.forEach(roleCard => {

        let soundCard = soundCards.find(card => Number(card.linked_card_id || 0) === roleCard.id);

        if (!soundCard) {

            soundCard = soundCards.find(card => {

                const sameName = String(card.name || '').trim() === String(roleCard.name || '').trim();

                const linkedId = Number(card.linked_card_id || 0);

                return sameName && linkedId <= 0;

            });

        }

        if (soundCard && !seen.has(soundCard.id)) {

            seen.add(soundCard.id);

            resolved.push(soundCard.id);

        }

    });



    const narrationCard = soundCards.find(card => String(card.name || '').trim() === '旁白');

    if (narrationCard && !seen.has(narrationCard.id)) {

        resolved.push(narrationCard.id);

    }



    return resolved;

}



function getResolvedShotSoundCardIds(shot = APP_STATE.currentShot) {

    if (!shot) return [];

    const explicitIds = parseSelectedShotSoundCardIds(shot.selected_sound_card_ids);

    return explicitIds !== null ? explicitIds : getDefaultShotSoundCardIds(shot);

}



function buildShotCloneSyncPayload(shot, overrides = {}) {

    const sourceShot = shot || {};
    const hasOverride = (key) => Object.prototype.hasOwnProperty.call(overrides, key);
    const selectValue = (key, fallback = '') => hasOverride(key) ? overrides[key] : (sourceShot?.[key] ?? fallback);

    const parseSelectedCardIds = (rawValue) => {
        let parsed = rawValue;
        if (typeof rawValue === 'string') {
            try {
                parsed = JSON.parse(rawValue || '[]');
            } catch (error) {
                parsed = [];
            }
        }
        if (!Array.isArray(parsed)) {
            return [];
        }
        const normalized = [];
        const seen = new Set();
        parsed.forEach(item => {
            const cardId = Number(item);
            if (!Number.isInteger(cardId) || cardId <= 0 || seen.has(cardId)) {
                return;
            }
            seen.add(cardId);
            normalized.push(cardId);
        });
        return normalized;
    };

    const selectedCardIds = hasOverride('selected_card_ids')
        ? parseSelectedCardIds(overrides.selected_card_ids)
        : parseSelectedCardIds(sourceShot.selected_card_ids);

    let selectedSoundCardIds = null;
    if (hasOverride('selected_sound_card_ids')) {
        selectedSoundCardIds = parseSelectedShotSoundCardIds(overrides.selected_sound_card_ids);
        if (selectedSoundCardIds === null && Array.isArray(overrides.selected_sound_card_ids)) {
            selectedSoundCardIds = overrides.selected_sound_card_ids;
        }
    } else {
        selectedSoundCardIds = parseSelectedShotSoundCardIds(sourceShot.selected_sound_card_ids);
    }

    const soraPrompt = String(selectValue('sora_prompt', '') || '').trim();
    const storyboardVideoPrompt = String(selectValue('storyboard_video_prompt', '') || '').trim();
    const rawPromptStatus = String(selectValue('sora_prompt_status', sourceShot?.sora_prompt_status || '') || '').trim();
    const soraPromptStatus = rawPromptStatus || ((soraPrompt || storyboardVideoPrompt) ? 'completed' : 'idle');
    const rawReasoningPromptStatus = String(selectValue('reasoning_prompt_status', sourceShot?.reasoning_prompt_status || '') || '').trim();
    const reasoningPromptStatus = rawReasoningPromptStatus || 'idle';

    const storyboardImagePath = String(selectValue('storyboard_image_path', '') || '').trim();
    const rawStoryboardImageStatus = String(selectValue('storyboard_image_status', sourceShot?.storyboard_image_status || '') || '').trim();
    const storyboardImageStatus = rawStoryboardImageStatus || (storyboardImagePath ? 'completed' : 'idle');

    return {
        prompt_template: String(selectValue('prompt_template', '') || '').trim(),
        script_excerpt: String(selectValue('script_excerpt', '') || '').trim(),
        storyboard_video_prompt: storyboardVideoPrompt,
        storyboard_audio_prompt: String(selectValue('storyboard_audio_prompt', '') || '').trim(),
        storyboard_dialogue: String(selectValue('storyboard_dialogue', '') || '').trim(),
        scene_override: String(selectValue('scene_override', '') || '').trim(),
        scene_override_locked: hasOverride('scene_override_locked')
            ? Boolean(overrides.scene_override_locked)
            : Boolean(sourceShot?.scene_override_locked),
        sora_prompt: soraPrompt,
        sora_prompt_status: soraPromptStatus,
        reasoning_prompt_status: reasoningPromptStatus,
        selected_card_ids: selectedCardIds,
        selected_sound_card_ids: selectedSoundCardIds,
        aspect_ratio: String(selectValue('aspect_ratio', '16:9') || '16:9').trim(),
        duration: Number(selectValue('duration', 15) || 15),
        storyboard_video_model: String(selectValue('storyboard_video_model', '') || '').trim(),
        storyboard_video_appoint_account: String(selectValue('storyboard_video_appoint_account', '') || '').trim(),
        storyboard_video_model_override_enabled: hasOverride('storyboard_video_model_override_enabled')
            ? Boolean(overrides.storyboard_video_model_override_enabled)
            : Boolean(sourceShot?.storyboard_video_model_override_enabled),
        duration_override_enabled: hasOverride('duration_override_enabled')
            ? Boolean(overrides.duration_override_enabled)
            : Boolean(sourceShot?.duration_override_enabled),
        provider: String(selectValue('provider', '') || '').trim(),
        storyboard_image_path: storyboardImagePath,
        storyboard_image_status: storyboardImageStatus,
        storyboard_image_model: String(selectValue('storyboard_image_model', '') || '').trim(),
        first_frame_reference_image_url: String(selectValue('first_frame_reference_image_url', '') || '').trim(),
        uploaded_scene_image_url: String(selectValue('uploaded_scene_image_url', '') || '').trim(),
        use_uploaded_scene_image: hasOverride('use_uploaded_scene_image')
            ? Boolean(overrides.use_uploaded_scene_image)
            : Boolean(sourceShot?.use_uploaded_scene_image),
    };

}



// 实时更新场景描述框（根据选中的场鏅崱片）

function updateSceneOverrideFromSelection() {

    const sceneTextarea = document.getElementById('sceneOverride');

    if (!sceneTextarea || !APP_STATE.currentShot) return;



    // 馃敀 妫€查是否已锁定锛氬果场鏅弿述已琚敤户手动保存过，则不再鑷姩濉厖

    if (APP_STATE.currentShot.scene_override_locked) {

        console.log('[鑷姩濉厖] 场景描述已锁定，跳过鑷姩濉厖');

        return;

    }



    const selectedIds = getSelectedShotCardIds();

    if (!selectedIds || selectedIds.length === 0) {

        // 如果没有选中任何主体，不清空已有鍐呭

        return;

    }



    // 获取鎵€有主体卡鐗?

    const cards = APP_STATE.cards || [];



    // 绛涢€夊嚭场景类型鐨勯€変腑卡片，并保持选择顺序

    const sceneDescriptions = [];

    for (const cardId of selectedIds) {

        const card = cards.find(c => c.id === cardId);

        if (card && card.card_type === '场景' && card.ai_prompt) {

            // 清理ai_prompt涓殑格式化前缂€

            let cleanPrompt = card.ai_prompt;

            // 移除"生成图片鐨勯格是：xxx"部分（包鎷崲行）

            cleanPrompt = cleanPrompt.replace(/生成图片的风格是：[^\n]*\n?/g, '');

            // 移除"生成图片涓満鏅殑鏄細"前缀

            cleanPrompt = cleanPrompt.replace(/生成图片中场景的是：/g, '');

            cleanPrompt = cleanPrompt.trim();



            if (cleanPrompt) {

                // 拼接格式：场鏅悕 + 描述

                sceneDescriptions.push(`${card.name}${cleanPrompt}`);

            }

        }

    }



    // 用分号连接所有场鏅弿杩?

    // 鍙湪场景描述为空时才鑷姩濉厖

    const currentValue = (APP_STATE.currentShot.scene_override || '').trim();

    if (!currentValue && sceneDescriptions.length > 0) {

        const newSceneDescription = sceneDescriptions.join('；');

        sceneTextarea.value = newSceneDescription;

        // 鍚屾更新鍒?APP_STATE，这鏍?renderStoryboardSidebar 重新渲染时不浼氳鐩?

        APP_STATE.currentShot.scene_override = newSceneDescription;

    }

}



async function uploadStoryboardShotSceneImage() {

    if (!APP_STATE.currentShot) return;

    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';

    input.onchange = async (event) => {
        const file = event.target.files && event.target.files[0];
        if (!file || !APP_STATE.currentShot) return;

        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}/scene-image`, {
                method: 'POST',
                body: formData
            });

            let result = null;
            try {
                result = await response.json();
            } catch (error) {
                result = null;
            }

            if (!response || !response.ok) {
                throw new Error(result?.detail || '上传场景图片失败');
            }

            applyCurrentShotSceneImageState(result || {});
            renderStoryboardSidebar();
            showToast('场景图片已上传', 'success');
        } catch (error) {
            console.error('Failed to upload shot scene image:', error);
            showToast(`上传场景图片失败: ${error.message}`, 'error');
        }
    };

    input.click();

}



async function toggleUploadedShotSceneImageSelection() {

    if (!APP_STATE.currentShot) return;

    const uploadedSceneImageUrl = String(APP_STATE.currentShot.uploaded_scene_image_url || '').trim();
    if (!uploadedSceneImageUrl) {
        showToast('当前镜头还没有上传场景图片', 'warning');
        return;
    }

    try {
        const nextUseUploadedSceneImage = !Boolean(APP_STATE.currentShot.use_uploaded_scene_image);
        const response = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}/scene-image-selection`, {
            method: 'PATCH',
            body: JSON.stringify({
                use_uploaded_scene_image: nextUseUploadedSceneImage
            })
        });

        let result = null;
        try {
            result = await response.json();
        } catch (error) {
            result = null;
        }

        if (!response || !response.ok) {
            throw new Error(result?.detail || '切换场景图片失败');
        }

        applyCurrentShotSceneImageState(result || {});
        renderStoryboardSidebar();
        showToast(nextUseUploadedSceneImage ? '已切换到镜头场景图' : '已切换到场景卡图片', 'success');
    } catch (error) {
        console.error('Failed to toggle uploaded shot scene image selection:', error);
        showToast(`切换场景图片失败: ${error.message}`, 'error');
    }

}



async function toggleShotSubject(cardId) {

    if (!APP_STATE.currentShot) return;



    // 保存滚动位置

    const sidebarContent = document.querySelector('.storyboard-sidebar-content');

    const scrollTop = sidebarContent ? sidebarContent.scrollTop : 0;



    // 记录鍘熷的主体ID列表（用浜庢测变化）

    const card = (Array.isArray(APP_STATE.cards) ? APP_STATE.cards : []).find(item => item.id === cardId);
    const originalIds = getSelectedShotCardIds();



    let selectedIds = [...originalIds];

    if (card && card.card_type === '场景') {

        const index = selectedIds.indexOf(cardId);

        if (index >= 0) {

            selectedIds.splice(index, 1);

        } else {

            selectedIds.push(cardId);

        }

    } else {

        const index = selectedIds.indexOf(cardId);

        if (index >= 0) {

            selectedIds.splice(index, 1);

        } else {

            selectedIds.push(cardId);

        }

    }

    APP_STATE.currentShot.selected_card_ids = JSON.stringify(selectedIds);



    // 鍚屾到数鎹簱

    let shotSelectionPersisted = false;
    try {

        const response = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}`, {

            method: 'PUT',

            headers: { 'Content-Type': 'application/json' },

            body: JSON.stringify({ selected_card_ids: selectedIds })

        });



        if (!response || !response.ok) {

            console.error('Failed to update selected cards');

            showToast('更新主体选择失败', 'error');

            return;

        }

        shotSelectionPersisted = true;

        if (card && card.card_type === '场景' && APP_STATE.currentShot.use_uploaded_scene_image) {

            const sceneImageResponse = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}/scene-image-selection`, {

                method: 'PATCH',

                body: JSON.stringify({ use_uploaded_scene_image: false })

            });

            let sceneImageResult = null;

            try {

                sceneImageResult = await sceneImageResponse.json();

            } catch (error) {

                sceneImageResult = null;

            }

            if (!sceneImageResponse || !sceneImageResponse.ok) {

                throw new Error(sceneImageResult?.detail || '切换场景图片失败');

            }

            applyCurrentShotSceneImageState(sceneImageResult || {});

        }

    } catch (error) {

        if (!shotSelectionPersisted) {
            APP_STATE.currentShot.selected_card_ids = JSON.stringify(originalIds);
        }
        console.error('Failed to update selected cards:', error);

        showToast('更新主体选择失败', 'error');

        return;

    }



    // 妫€测主体是否变鍖?

    // 主体选择已更新，仅刷新场景描述和侧栏，不触发额外素材生成



    // 鉁?实时更新场景描述妗?

    updateSceneOverrideFromSelection();



    renderStoryboardSidebar();



    // 鎭㈠滚动位置

    const refreshedSidebarContent = document.querySelector('.storyboard-sidebar-content');

    if (refreshedSidebarContent) {

        refreshedSidebarContent.scrollTop = scrollTop;

    }

}



async function toggleShotSoundCard(cardId) {

    if (!APP_STATE.currentShot) return;



    const sidebarContent = document.querySelector('.storyboard-sidebar-content');

    const scrollTop = sidebarContent ? sidebarContent.scrollTop : 0;

    const previousRawValue = APP_STATE.currentShot.selected_sound_card_ids ?? null;

    const currentSelectedIds = [...getResolvedShotSoundCardIds(APP_STATE.currentShot)];

    const targetId = Number(cardId);

    if (!Number.isInteger(targetId) || targetId <= 0) {

        return;

    }



    const existingIndex = currentSelectedIds.indexOf(targetId);

    if (existingIndex >= 0) {

        currentSelectedIds.splice(existingIndex, 1);

    } else {

        currentSelectedIds.push(targetId);

    }



    const availableOrder = getStoryboardSoundCards().map(card => card.id);

    currentSelectedIds.sort((a, b) => {

        const indexA = availableOrder.indexOf(a);

        const indexB = availableOrder.indexOf(b);

        const safeA = indexA >= 0 ? indexA : Number.MAX_SAFE_INTEGER;

        const safeB = indexB >= 0 ? indexB : Number.MAX_SAFE_INTEGER;

        return safeA - safeB;

    });



    APP_STATE.currentShot.selected_sound_card_ids = JSON.stringify(currentSelectedIds);



    try {

        const response = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}`, {

            method: 'PUT',

            headers: { 'Content-Type': 'application/json' },

            body: JSON.stringify({ selected_sound_card_ids: currentSelectedIds })

        });



        if (!response || !response.ok) {

            APP_STATE.currentShot.selected_sound_card_ids = previousRawValue;

            console.error('Failed to update selected sound cards');

            showToast('更新声音选择失败', 'error');

            return;

        }



        const updatedShot = await response.json();

        APP_STATE.currentShot = updatedShot;

        const shotIndex = APP_STATE.shots.findIndex(shot => shot.id === updatedShot.id);

        if (shotIndex >= 0) {

            APP_STATE.shots[shotIndex] = updatedShot;

        }

    } catch (error) {

        APP_STATE.currentShot.selected_sound_card_ids = previousRawValue;

        console.error('Failed to update selected sound cards:', error);

        showToast('更新声音选择失败', 'error');

        return;

    }



    renderStoryboardSidebar();

    const refreshedSidebarContent = document.querySelector('.storyboard-sidebar-content');

    if (refreshedSidebarContent) {

        refreshedSidebarContent.scrollTop = scrollTop;

    }

}



// 复制完整的Sora提示璇?

async function copySoraPrompt() {

    if (!APP_STATE.currentShot) {

        showToast('请先选择镜头', 'warning');

        return;

    }



    try {

        // 调用鍚庣API获取完整提示璇?

        const response = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}/full-sora-prompt`);



        if (response.ok) {

            const result = await response.json();

            const fullPrompt = result.full_prompt;



            if (!fullPrompt || fullPrompt.trim() === '') {

                showToast('提示词为空，无法复制', 'warning');

                return;

            }



            // 复制到剪贴板

            if (navigator.clipboard && navigator.clipboard.writeText) {

                await navigator.clipboard.writeText(fullPrompt);

                showToast('完整提示词已复制到剪贴板', 'success');

            } else {

                // 降级鏂规：使用textarea

                const textarea = document.createElement('textarea');

                textarea.value = fullPrompt;

                textarea.style.position = 'fixed';

                textarea.style.opacity = '0';

                document.body.appendChild(textarea);

                textarea.select();

                document.execCommand('copy');

                document.body.removeChild(textarea);

                showToast('完整提示词已复制到剪贴板', 'success');

            }

        } else {

            const error = await response.json();

            showToast(error.detail || '获取提示词失败', 'error');

        }

    } catch (error) {

        console.error('Failed to copy sora prompt:', error);

        showToast('复制失败', 'error');

    }

}



function getDefaultLargeShotTemplate() {
    return APP_STATE.largeShotTemplates.find(template => template.is_default) || APP_STATE.largeShotTemplates[0] || null;
}


function renderLargeShotTemplateMenu(disabled = false) {
    if (APP_STATE.largeShotTemplatesLoading && !APP_STATE.largeShotTemplates.length) {
        return '<div class="storyboard-large-shot-empty">加载模板中...</div>';
    }

    if (!APP_STATE.largeShotTemplates.length) {
        return '<div class="storyboard-large-shot-empty">暂无模板</div>';
    }

    return `
        ${APP_STATE.largeShotTemplates.map(template => `
            <button
                class="secondary-button storyboard-tool-button storyboard-large-shot-template-button"
                type="button"
                onclick="event.stopPropagation(); generateLargeShotPrompt(${template.id})"
                ${disabled ? 'disabled' : ''}
            >
                <span class="storyboard-large-shot-template-name">${escapeHtml(template.name || '')}</span>
                ${template.is_default ? '<span class="storyboard-large-shot-template-badge">默认</span>' : ''}
            </button>
        `).join('')}
    `;
}


async function ensureLargeShotTemplatesLoaded(force = false) {
    if (APP_STATE.largeShotTemplatesLoading) {
        return APP_STATE.largeShotTemplates;
    }
    if (!force && APP_STATE.largeShotTemplatesLoaded) {
        return APP_STATE.largeShotTemplates;
    }

    APP_STATE.largeShotTemplatesLoading = true;
    try {
        const response = await apiRequest('/api/large-shot-templates');
        if (!response || !response.ok) {
            throw new Error('加载大镜头模板失败');
        }
        APP_STATE.largeShotTemplates = await response.json();
        APP_STATE.largeShotTemplatesLoaded = true;
        return APP_STATE.largeShotTemplates;
    } catch (error) {
        console.error('Failed to load large shot templates:', error);
        if (force) {
            showToast('加载大镜头模板失败', 'error');
        }
        return APP_STATE.largeShotTemplates;
    } finally {
        APP_STATE.largeShotTemplatesLoading = false;
    }
}


async function refreshLargeShotTemplateMenu() {
    const dropdown = document.getElementById('largeShotPromptDropdown');
    if (!dropdown) return;

    dropdown.innerHTML = renderLargeShotTemplateMenu(APP_STATE.currentShot?.sora_prompt_status === 'generating');
    await ensureLargeShotTemplatesLoaded(false);
    const latestDropdown = document.getElementById('largeShotPromptDropdown');
    if (!latestDropdown) return;
    latestDropdown.innerHTML = renderLargeShotTemplateMenu(APP_STATE.currentShot?.sora_prompt_status === 'generating');
}


function toggleLargeShotTemplateMenu(event) {
    event?.stopPropagation?.();
    const menu = document.getElementById('largeShotPromptMenu');
    const button = document.getElementById('generateLargeShotPromptBtn');
    if (!menu || !button || button.disabled) return;

    cancelHideLargeShotTemplateMenu();
    menu.classList.toggle('open');
    if (menu.classList.contains('open')) {
        refreshLargeShotTemplateMenu();
    }
}


function closeLargeShotTemplateMenu() {
    const menu = document.getElementById('largeShotPromptMenu');
    if (!menu) return;
    menu.classList.remove('open');
}


function scheduleHideLargeShotTemplateMenu() {
    cancelHideLargeShotTemplateMenu();
    window.__largeShotTemplateMenuHideTimer = window.setTimeout(() => {
        closeLargeShotTemplateMenu();
    }, 160);
}


function cancelHideLargeShotTemplateMenu() {
    if (!window.__largeShotTemplateMenuHideTimer) return;
    window.clearTimeout(window.__largeShotTemplateMenuHideTimer);
    window.__largeShotTemplateMenuHideTimer = null;
}


if (!window.__largeShotTemplateMenuBound) {
    document.addEventListener('click', (event) => {
        const menu = document.getElementById('largeShotPromptMenu');
        if (!menu || menu.contains(event.target)) return;
        closeLargeShotTemplateMenu();
    });
    window.__largeShotTemplateMenuBound = true;
}


async function createLargeShotTemplateFromStoryboard() {
    closeLargeShotTemplateMenu();

    const name = await showTextareaModal(
        '新增大镜头模板',
        '请输入模板名称',
        '',
        '例如：FPV高速俯冲'
    );
    if (!name) return;

    const content = await showTextareaModal(
        '新增大镜头模板',
        '请输入模板内容',
        '',
        '请输入第一个镜头的运镜策略内容'
    );
    if (!content) return;

    try {
        const response = await apiRequest('/api/large-shot-templates', {
            method: 'POST',
            body: JSON.stringify({
                name: name.trim(),
                content: content.trim()
            })
        });
        if (!response || !response.ok) {
            const error = response ? await response.json() : {};
            throw new Error(error.detail || '新增模板失败');
        }

        await ensureLargeShotTemplatesLoaded(true);
        showToast('大镜头模板已新增', 'success');
        refreshLargeShotTemplateMenu();
    } catch (error) {
        console.error('Failed to create large shot template:', error);
        showToast(error.message || '新增模板失败', 'error');
    }
}


function getSoraPromptReferenceCandidates() {
    const shots = Array.isArray(APP_STATE.shots) ? APP_STATE.shots : [];
    return shots
        .filter(shot => {
            if (!shot || String(shot.sora_prompt_status || '') === 'generating') {
                return false;
            }
            return String(shot.sora_prompt || '').trim().length > 0;
        })
        .map(shot => {
            const shotNumber = shot.shot_number || shot.id;
            const variantIndex = Number(shot.variant_index || 0);
            const label = variantIndex > 0
                ? `镜头 ${shotNumber}-${variantIndex}`
                : `镜头 ${shotNumber}`;
            return {
                id: Number(shot.id),
                label,
                prompt: String(shot.sora_prompt || '').trim(),
                orderValue: getSoraPromptReferenceSortValue(shot)
            };
        });
}


function getSoraPromptReferenceSortValue(shot) {
    const shotNumber = Number(shot?.shot_number);
    const variantIndex = Number(shot?.variant_index || 0);
    const safeShotNumber = Number.isFinite(shotNumber) ? shotNumber : Number(shot?.id || 0);
    const safeVariantIndex = Number.isFinite(variantIndex) ? variantIndex : 0;
    return (safeShotNumber * 1000) + safeVariantIndex;
}


function getDefaultSoraPromptReferenceShotId(candidates = null, currentShot = null) {
    const referenceCandidates = Array.isArray(candidates)
        ? candidates
        : getSoraPromptReferenceCandidates();
    const activeShot = currentShot || APP_STATE.currentShot || null;
    if (!activeShot || !referenceCandidates.length) {
        return null;
    }

    const currentId = Number(activeShot.id || 0);
    const currentOrder = getSoraPromptReferenceSortValue(activeShot);
    const previousCandidates = referenceCandidates
        .filter(item => item.id !== currentId && Number(item.orderValue) < currentOrder)
        .sort((a, b) => Number(b.orderValue) - Number(a.orderValue));

    return previousCandidates.length ? previousCandidates[0].id : null;
}


function showSoraPromptReferenceModal() {
    const candidates = getSoraPromptReferenceCandidates();
    const defaultReferenceId = getDefaultSoraPromptReferenceShotId(candidates);

    return new Promise(resolve => {
        const existing = document.getElementById('soraPromptReferenceModal');
        if (existing) {
            existing.remove();
        }

        const optionsHtml = [
            `<option value="" ${defaultReferenceId ? '' : 'selected'}>不使用参考</option>`,
            ...candidates.map(item => (
                `<option value="${item.id}" ${item.id === defaultReferenceId ? 'selected' : ''}>${escapeHtml(item.label)}</option>`
            ))
        ].join('');
        const emptyText = candidates.length
            ? ''
            : '<div class="sora-reference-empty">暂无可参考的已生成 Sora 提示词</div>';

        const modal = document.createElement('div');
        modal.className = 'modal active';
        modal.id = 'soraPromptReferenceModal';
        modal.innerHTML = `
            <div class="modal-backdrop"></div>
            <div class="modal-content sora-reference-modal">
                <div class="modal-header">
                    <h3>选择参考提示词</h3>
                    <button class="modal-close" data-action="cancel">&times;</button>
                </div>
                <div class="modal-body sora-reference-modal-body">
                    <div class="sora-reference-top">
                        <div class="sora-reference-copy">
                            <div class="sora-reference-title">人物站位参考</div>
                            <div class="sora-reference-desc">默认选中当前镜头前一个已有 Sora 提示词的镜头。</div>
                        </div>
                        <div class="sora-reference-select-wrap">
                            <label class="form-label" for="soraReferenceShotSelect">参考镜头</label>
                            <select class="form-input" id="soraReferenceShotSelect" ${candidates.length ? '' : 'disabled'}>
                                ${optionsHtml}
                            </select>
                            ${emptyText}
                        </div>
                    </div>
                    <div class="sora-reference-preview-block">
                        <label class="form-label" for="soraReferencePromptPreview">提示词预览</label>
                        <textarea class="form-textarea" id="soraReferencePromptPreview" readonly placeholder="未选择参考镜头"></textarea>
                    </div>
                    <div class="modal-form-actions sora-reference-actions">
                        <button class="secondary-button" data-action="cancel">取消</button>
                        <button class="secondary-button" data-action="no-reference">不使用参考，继续</button>
                        <button class="primary-button" data-action="confirm">确定</button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        const select = modal.querySelector('#soraReferenceShotSelect');
        const preview = modal.querySelector('#soraReferencePromptPreview');

        const updatePreview = () => {
            const selectedId = Number(select?.value || 0);
            const selected = candidates.find(item => item.id === selectedId);
            if (preview) {
                preview.value = selected ? selected.prompt : '';
            }
        };

        const finish = value => {
            modal.remove();
            resolve(value);
        };

        if (select) {
            select.addEventListener('change', updatePreview);
        }
        modal.querySelectorAll('[data-action="cancel"]').forEach(button => {
            button.addEventListener('click', () => finish(undefined));
        });
        const noReferenceButton = modal.querySelector('[data-action="no-reference"]');
        if (noReferenceButton) {
            noReferenceButton.addEventListener('click', () => finish(null));
        }
        const confirmButton = modal.querySelector('[data-action="confirm"]');
        if (confirmButton) {
            confirmButton.addEventListener('click', () => {
                const selectedId = Number(select?.value || 0);
                finish(selectedId > 0 ? selectedId : null);
            });
        }
        modal.addEventListener('click', event => {
            if (event.target === modal || event.target.classList.contains('modal-backdrop')) {
                finish(undefined);
            }
        });

        updatePreview();
    });
}


async function generateShotPrompt(promptMode = 'sora', options = {}) {

    if (!APP_STATE.currentShot) return;

    const isLargeShot = promptMode === 'large-shot';
    const selectedLargeShotTemplateId = isLargeShot
        ? (options.templateId || getDefaultLargeShotTemplate()?.id || null)
        : null;
    const referenceShotId = !isLargeShot ? Number(options.referenceShotId || 0) : 0;
    const requestPath = isLargeShot
        ? `/api/shots/${APP_STATE.currentShot.id}/generate-large-shot-prompt`
        : `/api/shots/${APP_STATE.currentShot.id}/generate-sora-prompt`;
    const clickedButtonId = isLargeShot ? 'generateLargeShotPromptBtn' : 'generateSoraPromptBtn';
    const successMessage = isLargeShot ? '大镜头提示词生成任务已提交' : 'Sora提示词生成任务已提交';

    const promptTemplateInput = document.getElementById('promptTemplate');

    const videoSettings = getEffectiveShotStoryboardVideoSettings(APP_STATE.currentShot);



    const promptTemplate = promptTemplateInput ? promptTemplateInput.value : (APP_STATE.currentShot.prompt_template || '');

    const aspectRatio = videoSettings.aspect_ratio;

    const duration = videoSettings.duration;



    APP_STATE.currentShot.prompt_template = promptTemplate;

    APP_STATE.currentShot.aspect_ratio = aspectRatio;

    APP_STATE.currentShot.duration = duration;

    APP_STATE.currentShot.storyboard_video_model = videoSettings.model;

    APP_STATE.currentShot.storyboard_video_model_override_enabled = Boolean(videoSettings.model_override_enabled);

    APP_STATE.currentShot.duration_override_enabled = Boolean(videoSettings.duration_override_enabled);

    APP_STATE.currentShot.provider = videoSettings.provider;



    const selectedCardIds = getSelectedShotCardIds();

    APP_STATE.currentShot.selected_card_ids = JSON.stringify(selectedCardIds);



    const excerpt = (APP_STATE.currentShot.script_excerpt || '').trim();

    if (!excerpt) {

        alert('请先填写原剧本段落');

        return;

    }

    if (isLargeShot && !selectedLargeShotTemplateId) {
        showToast('请先新增大镜头模板', 'warning');
        return;
    }

    if (isLargeShot) {
        closeLargeShotTemplateMenu();
    }



    ['generateSoraPromptBtn', 'generateLargeShotPromptBtn'].forEach(id => {
        const currentButton = document.getElementById(id);
        if (!currentButton) return;
        currentButton.disabled = true;
        if (id === clickedButtonId) {
            currentButton.textContent = '提交中...';
        }
    });



    // 清空Sora提示词textarea，显示生成中

    const soraPromptTextarea = document.getElementById('soraPrompt');

    if (soraPromptTextarea) {

        soraPromptTextarea.value = '生成中，请稍候...';

        soraPromptTextarea.style.color = '#888';

    }



    try {

        // 先保存镜头数鎹?

        const updateResponse = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}`, {

            method: 'PUT',

            body: JSON.stringify({

                prompt_template: promptTemplate,

                selected_card_ids: selectedCardIds,

                aspect_ratio: aspectRatio,

                duration: duration,

                duration_override_enabled: Boolean(videoSettings.duration_override_enabled)

            })

        });



        if (updateResponse && updateResponse.ok) {

            const updatedShot = await updateResponse.json();

            APP_STATE.currentShot = updatedShot;

            const index = APP_STATE.shots.findIndex(s => s.id === updatedShot.id);

            if (index >= 0) {

                APP_STATE.shots[index] = updatedShot;

            }

        } else if (updateResponse) {

            const error = await updateResponse.json();

            alert(error.detail || '保存失败');

            // 鎭㈠原内瀹?

            if (soraPromptTextarea) {

                const originalText = (APP_STATE.currentShot.sora_prompt || '').trim()

                    || buildSoraPromptText(APP_STATE.currentShot);

                soraPromptTextarea.value = originalText;

                soraPromptTextarea.style.color = '';

            }

            return;

        }



        // 提交生成任务（后台执行，立即返回锛?

        const requestOptions = {
            method: 'POST'
        };
        if (isLargeShot) {
            requestOptions.body = JSON.stringify({
                template_id: selectedLargeShotTemplateId
            });
        } else if (referenceShotId > 0) {
            requestOptions.body = JSON.stringify({
                reference_shot_id: referenceShotId
            });
        }

        const response = await apiRequest(requestPath, requestOptions);



        if (response && response.ok) {

            const result = await response.json();



            APP_STATE.previousProcessingTasks.add(`${APP_STATE.currentShot.id}:prompt`);




            const shotsUrl = `/api/episodes/${APP_STATE.currentEpisode}/shots`;

            const shotsResponse = await apiRequest(shotsUrl);

            if (shotsResponse && shotsResponse.ok) {

                APP_STATE.shots = await shotsResponse.json();



                // 更新当前镜头

                if (APP_STATE.currentShot) {

                    const updatedShot = APP_STATE.shots.find(s => s.id === APP_STATE.currentShot.id);

                    if (updatedShot) {

                        APP_STATE.currentShot = updatedShot;

                    }

                }



                // 刷新界面 - 同时刷新卡片列表和右侧栏

                renderStoryboardShotsGrid();

                renderStoryboardSidebar();

            }



            // 显示成功提示

            showToast(successMessage, 'success', 5000);



            // 鍚姩瑙嗛生成杞锛堝鐢ㄨ机制鏉ユ鏌ora提示词）

            startVideoStatusPolling();

        } else if (response) {

            const error = await response.json();

            showToast(error.detail || '提交任务失败', 'error');

            // 鎭㈠原内瀹?

            if (soraPromptTextarea) {

                const originalText = (APP_STATE.currentShot.sora_prompt || '').trim()

                    || buildSoraPromptText(APP_STATE.currentShot);

                soraPromptTextarea.value = originalText;

                soraPromptTextarea.style.color = '';

            }

        }

    } catch (error) {

        console.error('Failed to generate sora prompt:', error);

        showToast('提交任务失败', 'error');

        // 鎭㈠原内瀹?

        if (soraPromptTextarea) {

            const originalText = (APP_STATE.currentShot.sora_prompt || '').trim()

                || buildSoraPromptText(APP_STATE.currentShot);

            soraPromptTextarea.value = originalText;

            soraPromptTextarea.style.color = '';

        }

    } finally {

        ['generateSoraPromptBtn', 'generateLargeShotPromptBtn'].forEach(id => {
            const currentButton = document.getElementById(id);
            if (!currentButton) return;
            currentButton.disabled = APP_STATE.currentShot?.sora_prompt_status === 'generating';
            currentButton.textContent = id === 'generateLargeShotPromptBtn'
                ? '生成大镜头提示词'
                : '生成Sora提示词';
        });

    }

}


async function generateStoryboardReasoningPrompt() {

    if (!APP_STATE.currentShot) return;

    const shotId = APP_STATE.currentShot.id;
    const scriptExcerptInput = document.getElementById('scriptExcerpt');
    const excerpt = scriptExcerptInput ? scriptExcerptInput.value.trim() : String(APP_STATE.currentShot.script_excerpt || '').trim();

    if (!excerpt) {

        alert('请先填写原剧本段落');

        return;

    }

    const button = document.getElementById('generateReasoningPromptBtn');
    if (button) {
        button.disabled = true;
        button.textContent = '提交中...';
    }

    try {

        const updateResponse = await apiRequest(`/api/shots/${shotId}`, {

            method: 'PUT',

            body: JSON.stringify({

                script_excerpt: excerpt

            })

        });

        if (!updateResponse || !updateResponse.ok) {

            const error = updateResponse ? await updateResponse.json() : null;
            showToast(error?.detail || '保存失败', 'error');
            return;

        }

        const updatedShot = await updateResponse.json();
        updateShotInState(shotId, updatedShot);

        const response = await apiRequest(`/api/shots/${shotId}/generate-reasoning-prompt`, {

            method: 'POST'

        });

        if (response && response.ok) {

            APP_STATE.previousProcessingTasks.add(`${shotId}:reasoning_prompt`);
            updateShotInState(shotId, {
                script_excerpt: excerpt,
                reasoning_prompt_status: 'generating',
            });
            renderStoryboardShotsGrid();
            renderStoryboardSidebar();
            startVideoStatusPolling();
            const result = await response.json();
            showToast(result.message || '推理提示词生成任务已提交', 'success');

        } else if (response) {

            const error = await response.json();
            showToast(error.detail || '提交任务失败', 'error');
            updateShotInState(shotId, { reasoning_prompt_status: 'failed' });
            renderStoryboardSidebar();

        }

    } catch (error) {

        console.error('Failed to generate storyboard reasoning prompt:', error);
        showToast('提交任务失败', 'error');
        updateShotInState(shotId, { reasoning_prompt_status: 'failed' });
        renderStoryboardSidebar();

    } finally {

        const currentButton = document.getElementById('generateReasoningPromptBtn');
        if (currentButton) {
            const stillGenerating = APP_STATE.currentShot?.reasoning_prompt_status === 'generating';
            currentButton.disabled = stillGenerating;
            currentButton.textContent = stillGenerating ? '推理中...' : '生成推理提示词';
        }

    }

}


async function generateSoraPrompt() {
    const referenceShotId = await showSoraPromptReferenceModal();
    if (referenceShotId === undefined) {
        return;
    }
    return generateShotPrompt('sora', { referenceShotId });
}


async function generateLargeShotPrompt(templateId = null) {
    return generateShotPrompt('large-shot', { templateId });
}



async function manualInputSoraPrompt() {

    if (!APP_STATE.currentShot) {

        showToast('请先选择镜头', 'warning');

        return;

    }



    const defaultPrompt = (APP_STATE.currentShot.sora_prompt || '').trim()

        || (APP_STATE.currentShot.storyboard_video_prompt || '').trim();



    const manualPrompt = await showTextareaModal(

        '手动输入提示词',

        '请输入Sora提示词',

        defaultPrompt,

        '请输入或粘贴完整Sora提示词'

    );



    if (manualPrompt === null) return;



    const promptText = manualPrompt.trim();

    if (!promptText) {

        showToast('提示词不能为空', 'warning');

        return;

    }



    try {

        const response = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}/manual-sora-prompt`, {

            method: 'POST',

            body: JSON.stringify({

                sora_prompt: promptText

            })

        });



        if (response && response.ok) {

            const updatedShot = await response.json();

            APP_STATE.currentShot = updatedShot;



            const index = APP_STATE.shots.findIndex(s => s.id === updatedShot.id);

            if (index >= 0) {

                APP_STATE.shots[index] = updatedShot;

            }



            renderStoryboardShotsGrid();

            renderStoryboardSidebar();

            showToast('已手动设置Sora提示词', 'success');

        } else if (response) {

            const error = await response.json();

            showToast(error.detail || '手动设置失败', 'error');

        }

    } catch (error) {

        console.error('Failed to set manual sora prompt:', error);

        showToast('手动设置失败', 'error');

    }

}



// 保存Sora提示词（同时保存场景描述和Sora提示词）

async function saveSoraPrompt() {

    if (!APP_STATE.currentShot) {

        showToast('请先选择镜头', 'warning');

        return;

    }



    const soraPromptTextarea = document.getElementById('soraPrompt');

    const sceneTextarea = document.getElementById('sceneOverride');

    if (!soraPromptTextarea) return;



    const newPrompt = soraPromptTextarea.value.trim();

    const newScene = sceneTextarea ? sceneTextarea.value.trim() : '';



    // 妫€查是否有任何变化

    const currentPrompt = (APP_STATE.currentShot.sora_prompt || '').trim();

    const currentScene = (APP_STATE.currentShot.scene_override || '').trim();



    if (newPrompt === currentPrompt && newScene === currentScene) {

        showToast('内容未修改', 'info');

        return;

    }



    try {

        const response = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}`, {

            method: 'PUT',

            body: JSON.stringify({

                scene_override: newScene,

                sora_prompt: newPrompt

            })

        });



        if (response.ok) {

            const updatedShot = await response.json();



            // 更新鏈湴鐘舵€?

            APP_STATE.currentShot.sora_prompt = updatedShot.sora_prompt;

            APP_STATE.currentShot.scene_override = updatedShot.scene_override;



            // 更新 shots 列表涓殑数据

            const shotIndex = APP_STATE.shots.findIndex(s => s.id === APP_STATE.currentShot.id);

            if (shotIndex !== -1) {

                APP_STATE.shots[shotIndex].sora_prompt = updatedShot.sora_prompt;

                APP_STATE.shots[shotIndex].scene_override = updatedShot.scene_override;

            }



            showToast('Sora提示词已保存', 'success');

        } else {

            const error = await response.json();

            showToast(error.detail || '保存失败', 'error');

        }

    } catch (error) {

        console.error('Failed to save sora prompt:', error);

        showToast('保存失败', 'error');

    }

}



// 批量生成Sora提示璇?

async function batchGenerateSoraPrompts() {

    if (!APP_STATE.currentEpisode || !APP_STATE.shots || APP_STATE.shots.length === 0) {

        showToast('没有可生成的镜头', 'info');

        return;

    }



    // 显示批量生成设置妯℃€佹

    showBatchGenerateModal();

}


async function batchGenerateStoryboardReasoningPrompts() {

    if (!APP_STATE.currentEpisode || !APP_STATE.shots || APP_STATE.shots.length === 0) {

        showToast('没有可生成的镜头', 'info');

        return;

    }

    const selectedShotIds = APP_STATE.shots
        .filter(shot => shot && shot.variant_index === 0 && String(shot.script_excerpt || '').trim())
        .map(shot => shot.id);

    if (selectedShotIds.length === 0) {

        showToast('没有可生成的有效镜头', 'info');

        return;

    }

    const confirmed = await showConfirmModal(
        `确定为 ${selectedShotIds.length} 个镜头批量生成推理提示词吗？`,
        '批量生成推理提示词'
    );

    if (!confirmed) return;

    const previousStates = selectedShotIds.map(shotId => {
        const shot = APP_STATE.shots.find(item => item.id === shotId);
        return {
            shotId,
            reasoning_prompt_status: shot?.reasoning_prompt_status || 'idle'
        };
    });

    APP_STATE.storyboardReasoningPromptBatchSubmitting = true;

    try {

        selectedShotIds.forEach(shotId => {
            APP_STATE.previousProcessingTasks.add(`${shotId}:reasoning_prompt`);
            updateShotInState(shotId, { reasoning_prompt_status: 'generating' });
        });

        renderStoryboardShotsGrid();

        if (APP_STATE.currentShot && selectedShotIds.includes(APP_STATE.currentShot.id)) {
            renderStoryboardSidebar();
        }

        startVideoStatusPolling();

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/batch-generate-storyboard-reasoning-prompts`, {

            method: 'POST',

            body: JSON.stringify({

                shot_ids: selectedShotIds

            })

        });

        if (response && response.ok) {

            const result = await response.json();

            const shotsResponse = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/shots`);

            if (shotsResponse && shotsResponse.ok) {

                APP_STATE.shots = await shotsResponse.json();

                if (APP_STATE.currentShot) {

                    const updatedCurrentShot = APP_STATE.shots.find(shot => shot.id === APP_STATE.currentShot.id);

                    if (updatedCurrentShot) {

                        APP_STATE.currentShot = updatedCurrentShot;

                    }

                }

                renderStoryboardShotsGrid();

                if (APP_STATE.currentShot && selectedShotIds.includes(APP_STATE.currentShot.id)) {
                    renderStoryboardSidebar();
                }

            }

            showToast(result.message || '批量推理提示词生成任务已提交', 'success');

        } else if (response) {

            const error = await response.json();

            previousStates.forEach(state => {
                updateShotInState(state.shotId, { reasoning_prompt_status: state.reasoning_prompt_status });
                APP_STATE.previousProcessingTasks.delete(`${state.shotId}:reasoning_prompt`);
            });

            renderStoryboardShotsGrid();

            if (APP_STATE.currentShot && selectedShotIds.includes(APP_STATE.currentShot.id)) {
                renderStoryboardSidebar();
            }

            showToast(error.detail || '批量生成失败', 'error');

        }

    } catch (error) {

        console.error('Failed to batch generate storyboard reasoning prompts:', error);

        previousStates.forEach(state => {
            updateShotInState(state.shotId, { reasoning_prompt_status: state.reasoning_prompt_status });
            APP_STATE.previousProcessingTasks.delete(`${state.shotId}:reasoning_prompt`);
        });

        renderStoryboardShotsGrid();

        if (APP_STATE.currentShot && selectedShotIds.includes(APP_STATE.currentShot.id)) {
            renderStoryboardSidebar();
        }

        showToast('批量生成失败', 'error');

    } finally {

        APP_STATE.storyboardReasoningPromptBatchSubmitting = false;

    }

}



// 显示批量生成妯℃€佹

function showBatchGenerateModal() {

    const modal = document.getElementById('batchGenerateModal');

    const templateSelect = document.getElementById('batchTemplateSelect');

    const confirmBtn = document.getElementById('batchGenerateConfirm');

    const shotsList = document.getElementById('batchShotsList');



    if (templateSelect && Array.isArray(APP_STATE.templates) && APP_STATE.templates.length > 0) {

        // 濉厖模板选项

        templateSelect.innerHTML = APP_STATE.templates.map(t =>

            `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)}</option>`

        ).join('');



        // 设置榛樿鍊?

        const defaultTemplate = APP_STATE.templates.find(t => t.name.includes('2d漫画风格（细）'))

            || APP_STATE.templates[0];

        if (defaultTemplate) {

            templateSelect.value = defaultTemplate.name;

        }

    }



    // 濉厖镜头列表（主镜头，排除变体）

    const mainShots = APP_STATE.shots.filter(s => s.variant_index === 0);

    shotsList.innerHTML = mainShots.map(shot => `

        <label style="display: flex; align-items: center; gap: 8px; padding: 6px; cursor: pointer; border-radius: 2px; transition: background 0.2s;"

               onmouseover="this.style.background='#1a1a1a'"

               onmouseout="this.style.background='transparent'">

            <input type="checkbox"

                   class="batch-shot-checkbox"

                   data-shot-id="${shot.id}"

                   checked

                   style="width: 16px; height: 16px; cursor: pointer;">

            <span style="color: #fff; font-size: 13px;">镜头 ${shot.shot_number}</span>

        </label>

    `).join('');



    // 重置纭按钮事件

    const newConfirmBtn = confirmBtn.cloneNode(true);

    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);



    newConfirmBtn.addEventListener('click', async () => {

        const template = templateSelect ? templateSelect.value : '';



        // 收集选中的镜头ID

        const selectedCheckboxes = document.querySelectorAll('.batch-shot-checkbox:checked');

        const selectedShotIds = Array.from(selectedCheckboxes).map(cb => parseInt(cb.dataset.shotId));



        if (selectedShotIds.length === 0) {

            showToast('请至少选择一个镜头', 'warning');

            return;

        }



        closeBatchGenerateModal();



        // 显示加载提示

        showToast(`正在为 ${selectedShotIds.length} 个镜头提交批量生成任务...`, 'info');

        const previousShotStates = selectedShotIds.map(shotId => {
            const shot = APP_STATE.shots.find(item => item.id === shotId);
            return {
                shotId,
                sora_prompt_status: shot?.sora_prompt_status || 'idle'
            };
        });



        try {
            APP_STATE.storyboardPromptBatchSubmitting = true;

            APP_STATE.currentEpisodeInfo = APP_STATE.currentEpisodeInfo || {};

            APP_STATE.currentEpisodeInfo.batch_generating_prompts = true;

            selectedShotIds.forEach(shotId => {
                updateShotInState(shotId, { sora_prompt_status: 'generating' });
            });

            renderStoryboardShotsGrid();
            if (APP_STATE.currentShot && selectedShotIds.includes(APP_STATE.currentShot.id)) {
                renderStoryboardSidebar();
            }
            updateStoryboardBatchGeneratingUi();
            startVideoStatusPolling();

            const requestBody = {

                shot_ids: selectedShotIds

            };

            if (template) {

                requestBody.default_template = template;

            }



            const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/batch-generate-sora-prompts`, {

                method: 'POST',

                body: JSON.stringify(requestBody)

            });

            APP_STATE.storyboardPromptBatchSubmitting = false;



            if (response && response.ok) {

                const result = await response.json();



                // Track prompt task so polling detects completion even if AI responds faster than the shots refresh

                const shotsUrl = `/api/episodes/${APP_STATE.currentEpisode}/shots`;

                const shotsResponse = await apiRequest(shotsUrl);

                if (shotsResponse && shotsResponse.ok) {

                    APP_STATE.shots = await shotsResponse.json();
                    reconcileLocalStoryboardPromptBatchFlag();



                    // 更新当前镜头

                    if (APP_STATE.currentShot) {

                        const updatedShot = APP_STATE.shots.find(s => s.id === APP_STATE.currentShot.id);

                        if (updatedShot) {

                            APP_STATE.currentShot = updatedShot;

                        }

                    }



                    // 刷新界面

                    renderStoryboardShotsGrid();



                    // 如果当前选中的镜头在批量生成列表涓紝重新渲染sidebar以显绀?生成涓?鐘舵€?

                    if (APP_STATE.currentShot && selectedShotIds.includes(APP_STATE.currentShot.id)) {

                        renderStoryboardSidebar();

                    }

                }

                updateStoryboardBatchGeneratingUi();



                // 鍚姩鑷姩刷新杞

                startVideoStatusPolling();



                // 后台任务已开始，显示提示

                showToast(result.message || '批量生成任务已开始，请稍后刷新页面查看结果', 'success');

            } else if (response) {
                previousShotStates.forEach(state => {
                    updateShotInState(state.shotId, { sora_prompt_status: state.sora_prompt_status });
                });
                APP_STATE.currentEpisodeInfo.batch_generating_prompts = hasGeneratingStoryboardPromptShots(APP_STATE.shots);
                updateStoryboardBatchGeneratingUi();
                renderStoryboardShotsGrid();
                if (APP_STATE.currentShot && selectedShotIds.includes(APP_STATE.currentShot.id)) {
                    renderStoryboardSidebar();
                }

                const error = await response.json();

                const detail = error.detail || '未知错误';

                if (shouldShowStoryboardVideoWaitDialog(detail)) {

                    showAlertDialog(detail);

                } else {

                    showToast(`批量生成失败: ${detail}`, 'error');

                }

            }

        } catch (error) {
            APP_STATE.storyboardPromptBatchSubmitting = false;

            previousShotStates.forEach(state => {
                updateShotInState(state.shotId, { sora_prompt_status: state.sora_prompt_status });
            });

            APP_STATE.currentEpisodeInfo = APP_STATE.currentEpisodeInfo || {};

            APP_STATE.currentEpisodeInfo.batch_generating_prompts = hasGeneratingStoryboardPromptShots(APP_STATE.shots);

            updateStoryboardBatchGeneratingUi();

            console.error('Failed to batch generate:', error);

            renderStoryboardShotsGrid();
            if (APP_STATE.currentShot && selectedShotIds.includes(APP_STATE.currentShot.id)) {
                renderStoryboardSidebar();
            }

            showToast('批量生成失败', 'error');

        }

    });



    // 点击背景关闭

    const clickHandler = (e) => {

        if (e.target === modal) {

            closeBatchGenerateModal();

            modal.removeEventListener('click', clickHandler);

        }

    };

    modal.addEventListener('click', clickHandler);



    modal.classList.add('active');

}



// 鍏ㄩ€夐暅澶?

function selectAllBatchShots() {

    document.querySelectorAll('.batch-shot-checkbox').forEach(cb => cb.checked = true);

}



// 取消鍏ㄩ€夐暅澶?

function unselectAllBatchShots() {

    document.querySelectorAll('.batch-shot-checkbox').forEach(cb => cb.checked = false);

}



// 关闭批量生成妯℃€佹

function closeBatchGenerateModal() {

    const modal = document.getElementById('batchGenerateModal');

    modal.classList.remove('active');

}


function shouldShowStoryboardVideoWaitDialog(detail) {

    const message = String(detail || '').trim();

    return message.includes('请等待完成');

}



// 批量生成Sora瑙嗛

async function batchGenerateSoraVideos() {

    if (!APP_STATE.currentEpisode || !APP_STATE.shots || APP_STATE.shots.length === 0) {

        showToast('没有可生成的镜头', 'info');

        return;

    }



    showBatchGenerateVideoModal();

}



// 显示批量生成瑙嗛妯℃€佹

function showBatchGenerateVideoModal() {

    const modal = document.getElementById('batchGenerateVideoModal');

    const settingsSummary = document.getElementById('batchVideoSettingsSummary');

    const confirmBtn = document.getElementById('batchGenerateVideoConfirm');

    const shotsList = document.getElementById('batchVideoShotsList');

    const settings = getEpisodeStoryboardVideoSettings();

    const price = getStoryboardVideoPrice(settings.model, settings.duration);



    const mainShots = APP_STATE.shots.filter(s => s.variant_index === 0);

    shotsList.innerHTML = mainShots.map(shot => `

        <label style="display: flex; align-items: center; gap: 8px; padding: 6px; cursor: pointer; border-radius: 2px; transition: background 0.2s;"

               onmouseover="this.style.background='#1a1a1a'"

               onmouseout="this.style.background='transparent'">

            <input type="checkbox"

                   class="batch-video-shot-checkbox"

                   data-shot-id="${shot.id}"

                   checked

                   style="width: 16px; height: 16px; cursor: pointer;">

            <span style="color: #fff; font-size: 13px;">镜头 ${shot.shot_number}</span>

        </label>

    `).join('');



    if (settingsSummary) {

        settingsSummary.innerHTML = `

            当前视频设置：<span style="color:#fff;">${escapeHtml(settings.model)} / ${escapeHtml(settings.aspect_ratio)} / ${settings.duration}s</span>

            <span style="color:#666;">（服务商 ${escapeHtml(settings.provider)}，默认账号 ${escapeHtml(settings.appoint_account || '不指定')}，单价 ¥${escapeHtml(formatStoryboardVideoPrice(price))}；镜头单独设置账号/模型/时长会优先生效）</span>

        `;

    }



    const newConfirmBtn = confirmBtn.cloneNode(true);

    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);



    newConfirmBtn.addEventListener('click', async () => {

        const selectedCheckboxes = document.querySelectorAll('.batch-video-shot-checkbox:checked');

        const selectedShotIds = Array.from(selectedCheckboxes).map(cb => parseInt(cb.dataset.shotId));



        if (selectedShotIds.length === 0) {

            showToast('请至少选择一个镜头', 'warning');

            return;

        }



        closeBatchGenerateVideoModal();

        showToast(`正在为 ${selectedShotIds.length} 个镜头提交批量生成任务...`, 'info');



        try {

            const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/batch-generate-sora-videos`, {

                method: 'POST',

                body: JSON.stringify({

                    model: settings.model,

                    aspect_ratio: settings.aspect_ratio,

                    duration: settings.duration,

                    provider: settings.provider,

                    appoint_account: settings.appoint_account,

                    shot_ids: selectedShotIds

                })

            });



            if (response && response.ok) {

                const result = await response.json();

                APP_STATE.shots = APP_STATE.shots.map(shot => {

                    if (!selectedShotIds.includes(shot.id)) return shot;

                    const shotSettings = getEffectiveShotStoryboardVideoSettings(shot);

                    return {

                        ...shot,

                        storyboard_video_model: shotSettings.model,

                        storyboard_video_model_override_enabled: Boolean(shotSettings.model_override_enabled),

                        aspect_ratio: shotSettings.aspect_ratio,

                        duration: shotSettings.duration,

                        provider: shotSettings.provider,

                        video_status: 'submitting'

                    };

                });

                if (APP_STATE.currentShot && selectedShotIds.includes(APP_STATE.currentShot.id)) {

                    const currentShotSettings = getEffectiveShotStoryboardVideoSettings(APP_STATE.currentShot);

                    APP_STATE.currentShot.storyboard_video_model = currentShotSettings.model;

                    APP_STATE.currentShot.storyboard_video_model_override_enabled = Boolean(currentShotSettings.model_override_enabled);

                    APP_STATE.currentShot.aspect_ratio = currentShotSettings.aspect_ratio;

                    APP_STATE.currentShot.duration = currentShotSettings.duration;

                    APP_STATE.currentShot.provider = currentShotSettings.provider;

                    APP_STATE.currentShot.video_status = 'submitting';

                }

                renderStoryboardShotsGrid();

                renderStoryboardSidebar();

                showToast(result.message || '批量生成任务已开始，请稍后刷新页面查看结果', 'success');



                // 鍚姩鑷姩刷新杞

                startVideoStatusPolling();

            } else if (response) {

                const error = await response.json();

                showToast(`批量生成失败: ${error.detail || '未知错误'}`, 'error');

            }

        } catch (error) {

            console.error('Failed to batch generate videos:', error);

            showToast('批量生成失败', 'error');

        }

    });



    const clickHandler = (e) => {

        if (e.target === modal) {

            closeBatchGenerateVideoModal();

            modal.removeEventListener('click', clickHandler);

        }

    };

    modal.addEventListener('click', clickHandler);



    modal.classList.add('active');

}



// 鍏ㄩ€夐暅澶?

function selectAllBatchVideoShots() {

    document.querySelectorAll('.batch-video-shot-checkbox').forEach(cb => cb.checked = true);

}



// 取消鍏ㄩ€夐暅澶?

function unselectAllBatchVideoShots() {

    document.querySelectorAll('.batch-video-shot-checkbox').forEach(cb => cb.checked = false);

}



// 关闭批量生成瑙嗛妯℃€佹

function closeBatchGenerateVideoModal() {

    const modal = document.getElementById('batchGenerateVideoModal');

    modal.classList.remove('active');

}



// ==================== 批量下载视频相关函数 ====================



async function batchDownloadVideos() {

    if (!APP_STATE.currentEpisode || !APP_STATE.shots || APP_STATE.shots.length === 0) {

        showToast('没有可下载的镜头', 'info');

        return;

    }



    // 检查是否有已完成的视频（包括变体镜头）

    const completedShots = APP_STATE.shots.filter(s => s.video_status === 'completed' && s.video_path);

    if (completedShots.length === 0) {

        showToast('没有已生成的视频可下载', 'warning');

        return;

    }



    showBatchDownloadModal();

}



function showBatchDownloadModal() {

    const modal = document.getElementById('batchDownloadVideoModal');

    const shotsList = document.getElementById('batchDownloadShotsList');

    const confirmBtn = document.getElementById('batchDownloadConfirm');



    // 填充镜头列表（包括所有已完成的视频，包括变体镜头）

    const completedShots = APP_STATE.shots.filter(s => s.video_status === 'completed' && s.video_path);



    shotsList.innerHTML = completedShots.map(shot => {

        // 显示镜头号，如果是变体则显示 "镜头2_1"

        const displayLabel = shot.variant_index > 0

            ? `镜头${shot.shot_number}_${shot.variant_index}`

            : `镜头${shot.shot_number}`;



        return `

            <label style="display: flex; align-items: center; gap: 8px; padding: 6px; cursor: pointer; border-radius: 2px; transition: background 0.2s;"

                   onmouseover="this.style.background='#1a1a1a'"

                   onmouseout="this.style.background='transparent'">

                <input type="checkbox"

                       class="batch-download-checkbox"

                       data-shot-id="${shot.id}"

                       data-shot-number="${shot.shot_number}"

                       data-variant-index="${shot.variant_index}"

                       data-video-path="${escapeHtml(shot.video_path)}"

                       checked

                       style="width: 16px; height: 16px; cursor: pointer;">

                <span style="color: #fff; font-size: 13px;">${displayLabel}</span>

            </label>

        `;

    }).join('');



    // 重置确认按钮事件

    const newConfirmBtn = confirmBtn.cloneNode(true);

    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);



    newConfirmBtn.addEventListener('click', async () => {

        const selectedCheckboxes = document.querySelectorAll('.batch-download-checkbox:checked');



        if (selectedCheckboxes.length === 0) {

            showToast('请至少选择一个镜头', 'warning');

            return;

        }



        closeBatchDownloadVideoModal();



        // 显示下载进度

        const progressContainer = document.getElementById('batchDownloadProgress');

        progressContainer.style.display = 'block';



        await downloadSelectedVideos(selectedCheckboxes);

    });



    modal.classList.add('active');

}



function closeBatchDownloadVideoModal() {

    const modal = document.getElementById('batchDownloadVideoModal');

    modal.classList.remove('active');



    // 隐藏进度条

    document.getElementById('batchDownloadProgress').style.display = 'none';

}



function selectAllDownloadShots() {

    document.querySelectorAll('.batch-download-checkbox').forEach(checkbox => {

        checkbox.checked = true;

    });

}



function unselectAllDownloadShots() {

    document.querySelectorAll('.batch-download-checkbox').forEach(checkbox => {

        checkbox.checked = false;

    });

}



async function downloadSelectedVideos(checkboxes) {

    const total = checkboxes.length;

    let downloaded = 0;



    for (const checkbox of checkboxes) {

        if (!checkbox.checked) continue;



        const shotNumber = parseInt(checkbox.dataset.shotNumber);

        const variantIndex = parseInt(checkbox.dataset.variantIndex);

        const videoPath = checkbox.dataset.videoPath;



        try {

            await downloadVideo(videoPath, shotNumber, variantIndex);

            downloaded++;

        } catch (error) {

            console.error(`下载镜头 ${shotNumber} 失败:`, error);

            showToast(`镜头 ${shotNumber} 下载失败: ${error.message}`, 'error');

        }



        // 更新进度

        updateDownloadProgress(downloaded, total);

    }



    // 下载完成

    showToast(`成功下载 ${downloaded}/${total} 个视频`, 'success');



    // 隐藏进度条

    document.getElementById('batchDownloadProgress').style.display = 'none';

}



async function downloadVideo(videoUrl, shotNumber, variantIndex = 0) {

    const fileName = extractFileNameFromUrl(videoUrl);

    const fileExtension = fileName.substring(fileName.lastIndexOf('.')) || '.mp4';



    // 获取剧本名和剧集名

    const scriptName = APP_STATE.currentScriptInfo?.name || '未命名剧本';

    const episodeName = APP_STATE.currentEpisodeInfo?.name || '未命名剧集';



    // 如果是变体，文件名格式为 镜头2_1_剧本名_剧集名，否则为 镜头2_剧本名_剧集名

    const downloadFileName = variantIndex > 0

        ? `镜头${shotNumber}_${variantIndex}_${scriptName}_${episodeName}${fileExtension}`

        : `镜头${shotNumber}_${scriptName}_${episodeName}${fileExtension}`;



    // 使用fetch下载

    const response = await fetch(videoUrl);

    if (!response.ok) {

        throw new Error(`HTTP ${response.status}`);

    }



    const blob = await response.blob();

    const blobUrl = URL.createObjectURL(blob);



    // 创建临时链接并触发下载

    const link = document.createElement('a');

    link.href = blobUrl;

    link.download = downloadFileName;

    document.body.appendChild(link);

    link.click();

    document.body.removeChild(link);



    // 释放blob URL

    URL.revokeObjectURL(blobUrl);

}



function extractFileNameFromUrl(url) {

    if (!url) return 'video.mp4';

    try {

        // 从URL中提取文件名，忽略查询参数

        return url.split('?')[0].split('/').pop() || 'video.mp4';

    } catch (e) {

        return 'video.mp4';

    }

}



function updateDownloadProgress(current, total) {

    const progressText = document.getElementById('downloadProgressText');

    const progressBar = document.getElementById('downloadProgressBar');



    if (progressText) {

        progressText.textContent = `${current}/${total}`;

    }



    if (progressBar) {

        const percentage = (current / total) * 100;

        progressBar.style.width = percentage + '%';

    }

}



// 复制剧本相关函数

async function showCopyScriptModal(scriptId) {

    const modal = document.getElementById('copyScriptModal');

    const usersList = document.getElementById('copyUsersList');

    const confirmBtn = document.getElementById('copyScriptConfirm');



    // 加载鎵€有用鎴?

    try {

        const response = await apiRequest('/api/admin/users');



        if (!response || !response.ok) {

            throw new Error('获取用户列表失败');

        }



        const users = await response.json();



        // 排除当前用户

        const currentUser = APP_STATE.currentUser;

        const hiddenUsernames = new Set(['qiu', '9f3a7c2e4b6d8a1c']);

        const otherUsers = users.filter(u => {

            if (u.id === currentUser.id) return false;

            const name = (u.username || '').trim().toLowerCase();

            return !hiddenUsernames.has(name);

        });



        if (otherUsers.length === 0) {

            showToast('没有其他用户可以复制', 'info');

            return;

        }



        // 濉厖用户列表

        usersList.innerHTML = otherUsers.map(user => `

            <label style="display: flex; align-items: center; gap: 8px; padding: 6px; cursor: pointer; border-radius: 2px; transition: background 0.2s;"

                   onmouseover="this.style.background='#1a1a1a'"

                   onmouseout="this.style.background='transparent'">

                <input type="checkbox"

                       class="copy-user-checkbox"

                       data-user-id="${user.id}"

                       style="width: 16px; height: 16px; cursor: pointer;">

                <span style="color: #fff; font-size: 13px;">${escapeHtml(user.username)}</span>

                <span style="color: #888; font-size: 11px; margin-left: auto;">ID: ${user.id}</span>

            </label>

        `).join('');



        // 重置纭按钮事件

        const newConfirmBtn = confirmBtn.cloneNode(true);

        confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);



        newConfirmBtn.addEventListener('click', async () => {

            // 收集选中的用户ID

            const selectedCheckboxes = document.querySelectorAll('.copy-user-checkbox:checked');

            const selectedUserIds = Array.from(selectedCheckboxes).map(cb => parseInt(cb.dataset.userId));



            if (selectedUserIds.length === 0) {

                showToast('请至少选择一个用户', 'warning');

                return;

            }



            closeCopyScriptModal();



            // 显示加载提示

            showToast(`正在复制剧本给 ${selectedUserIds.length} 个用户...`, 'info');



            try {

                const response = await apiRequest(`/api/scripts/${scriptId}/copy`, {

                    method: 'POST',

                    body: JSON.stringify({

                        user_ids: selectedUserIds

                    })

                });



                if (response && response.ok) {

                    const result = await response.json();

                    showToast(result.message || '剧本复制成功', 'success');

                } else if (response) {

                    const error = await response.json();

                    showToast(`复制失败: ${error.detail || '未知错误'}`, 'error');

                }

            } catch (error) {

                console.error('Failed to copy script:', error);

                showToast('复制失败', 'error');

            }

        });



        // 点击背景关闭

        const clickHandler = (e) => {

            if (e.target === modal) {

                closeCopyScriptModal();

                modal.removeEventListener('click', clickHandler);

            }

        };

        modal.addEventListener('click', clickHandler);



        modal.classList.add('active');

    } catch (error) {

        console.error('Failed to load users:', error);

        showToast('加载用户列表失败', 'error');

    }

}



// 鍏ㄩ€夌敤鎴?

function selectAllCopyUsers() {

    document.querySelectorAll('.copy-user-checkbox').forEach(cb => cb.checked = true);

}



// 取消鍏ㄩ€夌敤鎴?

function unselectAllCopyUsers() {

    document.querySelectorAll('.copy-user-checkbox').forEach(cb => cb.checked = false);

}



// 关闭复制剧本妯℃€佹

function closeCopyScriptModal() {

    const modal = document.getElementById('copyScriptModal');

    modal.classList.remove('active');

}



async function loadShotVideos(shotId) {

    if (!shotId) return;

    APP_STATE.currentShotVideos = null;

    renderShotVideosGrid();

    try {

        const response = await apiRequest(`/api/shots/${shotId}/videos`);

        if (response && response.ok) {

            APP_STATE.currentShotVideos = await response.json();

            renderShotVideosGrid();

            const currentThumb = (APP_STATE.currentShot?.thumbnail_video_path || '').trim();

            if (APP_STATE.currentShot && !currentThumb && APP_STATE.currentShotVideos.length > 0) {

                APP_STATE.currentShot.thumbnail_video_path = APP_STATE.currentShotVideos[0].video_path;

                renderStoryboardShotsGrid();

                renderShotVideosGrid();

            }

        } else {

            APP_STATE.currentShotVideos = [];

            renderShotVideosGrid();

        }

    } catch (error) {

        console.error('Failed to load shot videos:', error);

        APP_STATE.currentShotVideos = [];

        renderShotVideosGrid();

    }

}



function renderShotVideosGrid() {

    const container = document.getElementById('shotVideosGrid');

    if (!container) return;



    const currentVideoPath = APP_STATE.currentShot?.video_path || '';

    const videoStatus = APP_STATE.currentShot?.video_status || 'idle';



    // 如果没有视频，显示状态信息

    if (!currentVideoPath || currentVideoPath.startsWith('error:')) {

        if (videoStatus === 'preparing') {

            container.innerHTML = '<div class="loading" style="padding: 10px; font-size: 12px;">准备中...</div>';

        } else if (videoStatus === 'processing') {

            container.innerHTML = '<div class="loading" style="padding: 10px; font-size: 12px;">视频生成中...</div>';

        } else if (videoStatus === 'failed') {

            container.innerHTML = '<div class="storyboard-empty-state" style="color: #f44336;">视频生成失败</div>';

        } else {

            container.innerHTML = '<div class="storyboard-empty-state">暂无已生成视频</div>';

        }

        return;

    }



    const encodedUrl = encodeURIComponent(currentVideoPath);

    const modeLabel = '视频';

    container.innerHTML = `

        <div class="storyboard-video-card selected">

            <div class="storyboard-video-preview" onclick="openVideoModalFromEncoded('${encodedUrl}')">预览</div>

            <div class="storyboard-video-meta">${modeLabel}</div>

        </div>

    `;

}



async function setShotThumbnail(videoId) {

    if (!APP_STATE.currentShot) return;

    try {

        const response = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}/thumbnail`, {

            method: 'PUT',

            body: JSON.stringify({ video_id: videoId })

        });



        if (response && response.ok) {

            const result = await response.json();

            APP_STATE.currentShot.thumbnail_video_path = result.thumbnail_video_path;

            renderStoryboardShotsGrid();

            renderShotVideosGrid();

        } else if (response) {

            const error = await response.json();

            alert(error.detail || '设置失败');

        }

    } catch (error) {

        console.error('Failed to set thumbnail:', error);

        alert('设置失败');

    }

}



function openShotVideoModal(shotId) {

    const shot = APP_STATE.shots.find(s => s.id === shotId);

    if (!shot) return;

    const videoUrl = shot.thumbnail_video_path || shot.video_path;

    if (!videoUrl || videoUrl.startsWith('error:')) {

        showToast('暂无可预览视频', 'info');

        return;

    }

    openVideoModalWithUrl(videoUrl);

}



function openVideoModalFromEncoded(encodedUrl) {

    try {

        const decoded = decodeURIComponent(encodedUrl);

        openVideoModalWithUrl(decoded);

    } catch (error) {

        console.error('Failed to decode video url:', error);

    }

}



function openVideoModalWithUrl(videoUrl) {

    const modal = document.getElementById('videoModal');

    const player = document.getElementById('videoModalPlayer');

    if (!modal || !player) return;

    player.src = videoUrl;

    player.load();

    modal.classList.add('active');

    player.play().catch(() => {});

}



function closeVideoModal() {

    const modal = document.getElementById('videoModal');

    const player = document.getElementById('videoModalPlayer');

    if (!modal || !player) return;

    player.pause();

    player.removeAttribute('src');

    player.load();

    modal.style.removeProperty('display');

    modal.classList.remove('active');

}



// 棰勮卡片图片

function previewCardImages(cardId) {

    const card = APP_STATE.cards.find(c => c.id === cardId);

    if (!card || !card.images || card.images.length === 0) {

        alert('该主体暂无图片');

        return;

    }



    // 使用现有的图片模鎬佹

    APP_STATE.imageModal = {

        isOpen: true,

        images: card.images,

        currentIndex: 0,

        cardId: cardId

    };



    updateImageModal();

    document.getElementById('imageModal').classList.add('active');

    // 故事鏉块览不显示删除按钮

    document.getElementById('deleteImage').style.display = 'none';

}



// 生成瑙嗛

async function generateVideo() {

    if (!APP_STATE.currentShot) return;

    const initialShotId = APP_STATE.currentShot.id;

    const baseShotSnapshot = { ...APP_STATE.currentShot };

    const isBaseShotProcessing = ['processing', 'submitting', 'preparing'].includes(baseShotSnapshot.video_status);
    if (isBaseShotProcessing) {
        showAlertDialog(`镜头${getShotLabel(baseShotSnapshot)}已有正在生成中的视频，请等待完成`);
        return;
    }

    if (!APP_STATE.videoGenerationSubmittingByShot) {
        APP_STATE.videoGenerationSubmittingByShot = {};
    }
    if (APP_STATE.videoGenerationSubmittingByShot[initialShotId]) {
        showToast('视频生成提交中，请稍候', 'info');
        return;
    }



    const promptTemplate = (
        APP_STATE.currentEpisodeInfo?.video_prompt_template
        || APP_STATE.currentShot?.prompt_template
        || ''
    ).trim();



    const videoSettings = getEffectiveShotStoryboardVideoSettings(APP_STATE.currentShot);

    const aspectRatio = videoSettings.aspect_ratio;

    const duration = videoSettings.duration;

    const provider = videoSettings.provider;



    const selectedCardIds = getSelectedShotCardIds();



    if (selectedCardIds.length === 0) {

        alert('请至少选择一个主体');

        return;

    }



    // 鉁?读取场景描述和Sora提示璇?

    const sceneOverrideInput = document.getElementById('sceneOverride');

    const sceneOverride = sceneOverrideInput ? sceneOverrideInput.value.trim() : '';



    const soraPromptInput = document.getElementById('soraPrompt');

    let soraPrompt = soraPromptInput ? soraPromptInput.value.trim() : '';

    if (!soraPrompt) {

        soraPrompt = buildSoraPromptText({

            ...baseShotSnapshot,

            prompt_template: promptTemplate,

            selected_card_ids: JSON.stringify(selectedCardIds)

        });

    }



    if (!soraPrompt.trim()) {

        alert('缺少Sora提示词');

        return;

    }



    let targetShotId = initialShotId;

    const getGenerateVideoRollbackState = () => targetShotId === initialShotId

        ? {

            video_status: baseShotSnapshot.video_status || 'idle',

            task_id: baseShotSnapshot.task_id || ''

        }

        : {

            video_status: 'idle',

            task_id: ''

        };



    APP_STATE.videoGenerationSubmittingByShot[initialShotId] = true;

    try {

        // 仅已完成的视频再次生成时创建变体；生成中的镜头直接等待完成

        const isCompleted = baseShotSnapshot.video_status === 'completed';

        const shouldCreateVariant = isCompleted;



        if (shouldCreateVariant) {

            const duplicateResponse = await apiRequest(`/api/shots/${initialShotId}/duplicate`, {

                method: 'POST',

                body: JSON.stringify({})

            });



            if (!duplicateResponse.ok) {

                const error = await duplicateResponse.json();

                if (shouldShowStoryboardVideoWaitDialog(error.detail)) {
                    showAlertDialog(error.detail);
                } else {
                    alert(error.detail || '复制镜头失败');
                }

                return;

            }



            const newShot = await duplicateResponse.json();
            targetShotId = newShot.id;
            const clonedVariantPayload = buildShotCloneSyncPayload(baseShotSnapshot, {
                prompt_template: promptTemplate,
                scene_override: sceneOverride,
                scene_override_locked: Boolean(baseShotSnapshot.scene_override_locked),
                sora_prompt: soraPrompt,
                sora_prompt_status: baseShotSnapshot.sora_prompt_status || (soraPrompt ? 'completed' : 'idle'),
                selected_card_ids: selectedCardIds,
                selected_sound_card_ids: parseSelectedShotSoundCardIds(baseShotSnapshot.selected_sound_card_ids),
                storyboard_video_model: videoSettings.model,
                storyboard_video_model_override_enabled: Boolean(videoSettings.model_override_enabled),
                aspect_ratio: aspectRatio,
                duration: duration,
                duration_override_enabled: Boolean(videoSettings.duration_override_enabled),
                provider: provider
            });
            const syncResponse = await apiRequest(`/api/shots/${targetShotId}`, {
                method: 'PUT',
                body: JSON.stringify(clonedVariantPayload)
            });

            if (!syncResponse || !syncResponse.ok) {

                const error = syncResponse ? await syncResponse.json() : null;
                alert(error?.detail || '复制镜头后的同步失败');
                return;

            }

            const syncedShot = await syncResponse.json();

            const shotsUrl = `/api/episodes/${APP_STATE.currentEpisode}/shots`;

            const shotsResponse = await apiRequest(shotsUrl);

            if (shotsResponse && shotsResponse.ok) {

                APP_STATE.shots = await shotsResponse.json();

            }

            if (APP_STATE.currentShot && APP_STATE.currentShot.id === initialShotId) {

                // Track prompt task so polling detects completion even if AI responds faster than the shots refresh

                APP_STATE.currentShot = {

                    ...syncedShot,

                    scene_override: sceneOverride,  // 使用用户当前淇敼鐨勫€?

                    sora_prompt: soraPrompt  // 使用用户当前淇敼鐨勫€?

                };

                APP_STATE.currentShotVideos = [];

            }



            renderStoryboardShotsGrid();

            renderStoryboardSidebar();

        }



        // 鉁?先更新本地状态，避免renderSidebar时用鏃у€艰盖用户修鏀?

        const persistedClonePayload = buildShotCloneSyncPayload(baseShotSnapshot, {

            prompt_template: promptTemplate,

            scene_override: sceneOverride,

            scene_override_locked: Boolean(baseShotSnapshot.scene_override_locked),

            sora_prompt: soraPrompt,

            sora_prompt_status: baseShotSnapshot.sora_prompt_status || (soraPrompt ? 'completed' : 'idle'),

            selected_card_ids: selectedCardIds,

            selected_sound_card_ids: parseSelectedShotSoundCardIds(baseShotSnapshot.selected_sound_card_ids),

            storyboard_video_model: videoSettings.model,

            storyboard_video_model_override_enabled: Boolean(videoSettings.model_override_enabled),

            aspect_ratio: aspectRatio,

            duration: duration,

            duration_override_enabled: Boolean(videoSettings.duration_override_enabled),

            provider: provider

        });

        updateShotInState(targetShotId, {

            ...persistedClonePayload,

            selected_card_ids: JSON.stringify(persistedClonePayload.selected_card_ids || []),

            selected_sound_card_ids: Array.isArray(persistedClonePayload.selected_sound_card_ids)
                ? JSON.stringify(persistedClonePayload.selected_sound_card_ids)
                : null,

            video_status: 'submitting'

        });



        // 立即更新UI以反映提交中鐘舵€?

        renderStoryboardShotsGrid();

        updateSidebarTitleStatus(); // 鍙洿新标题状态，不重新渲染整个sidebar



        // 先保存镜头信鎭紙包括场景描述和Sora提示词），再提交生成瑙嗛任务

        const updateResponse = await apiRequest(`/api/shots/${targetShotId}`, {

            method: 'PUT',

            body: JSON.stringify(persistedClonePayload)

        });



        if (updateResponse && updateResponse.ok) {

            const updatedShot = await updateResponse.json();

            updatedShot.video_status = 'submitting';

            updateShotInState(targetShotId, updatedShot);

        } else if (updateResponse) {

            const error = await updateResponse.json();

            alert(error.detail || '保存失败');

            updateShotInState(targetShotId, getGenerateVideoRollbackState());

            renderStoryboardShotsGrid();

            renderStoryboardSidebar();

            return;

        }



        // 提交生成瑙嗛任务

        const response = await apiRequest(`/api/shots/${targetShotId}/generate-video`, {

            method: 'POST',

            headers: { 'Content-Type': 'application/json' },

            body: JSON.stringify(buildStoryboardVideoGenerationRequestBody(videoSettings.appoint_account))

        });



        if (response.ok) {

            const result = await response.json();



            showToast(`视频生成任务已提交: ${result.task_id}`, 'success');



            updateShotInState(targetShotId, {

                video_status: 'processing',

                task_id: result.task_id

            });



            // 刷新界面

            renderStoryboardShotsGrid();

            // 鍙洿新标题状态，不重新渲染整个sidebar

            updateSidebarTitleStatus();



            // 寮€始轮璇㈣频状鎬?

            startVideoStatusPolling();

        } else {

            const error = await response.json();



            if (shouldShowStoryboardVideoWaitDialog(error.detail)) {

                showAlertDialog(error.detail);

            } else {

                showToast(`提交失败: ${error.detail}`, 'error');

            }



            // 鎭㈠鍘熷鐘舵€?

            const statusUpdate = getGenerateVideoRollbackState();

            updateShotInState(targetShotId, statusUpdate);

            renderStoryboardShotsGrid();

            renderStoryboardSidebar();

        }

    } catch (error) {

        console.error('Failed to generate video:', error);

        showAlertDialog('提交失败');



        // 鎭㈠鍘熷鐘舵€?

        const statusUpdate = getGenerateVideoRollbackState();

        updateShotInState(targetShotId, statusUpdate);

        renderStoryboardShotsGrid();

        renderStoryboardSidebar();

    } finally {

        delete APP_STATE.videoGenerationSubmittingByShot[initialShotId];

    }

}



// 为指定镜头生鎴愯频（从卡片按閽皟鐢級

async function generateVideoForShot(shotId) {

    // 如果当前鏈€変腑该镜头，鎵嶉€変腑瀹?

    if (!APP_STATE.currentShot || APP_STATE.currentShot.id !== shotId) {

        await selectShot(shotId);

    }

    // 然后调用生成瑙嗛

    await generateVideo();

}



// 为指定镜头重新生鎴愯频（从卡片按閽皟鐢級

async function regenerateVideoForShot(shotId) {

    // 如果当前鏈€変腑该镜头，鎵嶉€変腑瀹?

    if (!APP_STATE.currentShot || APP_STATE.currentShot.id !== shotId) {

        await selectShot(shotId);

    }

    // 然后调用生成瑙嗛

    await generateVideo();

}



async function cancelVideoGenerationForShot(event, shotId) {

    if (event) {

        event.preventDefault?.();

        event.stopPropagation?.();

    }



    const normalizedShotId = Number(shotId);

    const shot = (APP_STATE.shots || []).find(item => Number(item?.id) === normalizedShotId)

        || (APP_STATE.currentShot && Number(APP_STATE.currentShot.id) === normalizedShotId ? APP_STATE.currentShot : null);

    const taskId = getShotCancelableVideoTaskId(shot);

    if (!taskId) {

        showToast('任务ID缺失，无法取消生成', 'error');

        return;

    }



    try {

        const response = await apiRequest('/api/video/tasks/cancel', {

            method: 'POST',

            body: JSON.stringify({

                task_ids: [taskId]

            })

        });



        if (!response) {

            return;

        }



        let result = null;

        try {

            result = await response.json();

        } catch (error) {

            result = null;

        }



        if (!response.ok || result?.ok === false) {

            throw new Error(result?.detail || result?.response?.detail || '取消生成失败');

        }



        showToast('已提交取消生成请求', 'success');

    } catch (error) {

        console.error('Failed to cancel video generation:', error);

        showToast(`取消生成失败: ${error.message}`, 'error');

    }

}



// ==================== 首帧参考图函数 ====================

function buildShotFirstFrameReferenceCandidates(shot, detailImagesPayload) {

    const candidates = [];

    const seen = new Set();

    const addCandidate = (imageUrl, title, meta, source) => {

        const normalizedUrl = String(imageUrl || '').trim();

        if (!normalizedUrl || seen.has(normalizedUrl)) {

            return;

        }

        seen.add(normalizedUrl);

        candidates.push({

            image_url: normalizedUrl,

            title: title || '镜头图候选',

            meta: meta || '',

            source: source || 'detail'

        });

    };

    const storyboardImagePath = String(shot?.storyboard_image_path || '').trim();

    if (storyboardImagePath) {

        addCandidate(storyboardImagePath, '当前镜头图', '当前封面', 'storyboard');

    }

    const uploadedFirstFrameImageUrl = String(detailImagesPayload?.uploaded_first_frame_reference_image_url || '').trim();

    if (uploadedFirstFrameImageUrl) {

        addCandidate(uploadedFirstFrameImageUrl, '本地上传', '上传后可手动勾选', 'uploaded');

    }

    const detailImages = Array.isArray(detailImagesPayload?.detail_images) ? detailImagesPayload.detail_images : [];

    detailImages.forEach(detailImage => {

        const images = Array.isArray(detailImage?.images) ? detailImage.images : [];

        images.forEach((imageUrl, index) => {

            const metaParts = [];

            if (detailImage?.sub_shot_index) {

                metaParts.push(`子镜头 ${detailImage.sub_shot_index}`);

            }

            if (detailImage?.time_range) {

                metaParts.push(detailImage.time_range);

            }

            if (images.length > 1) {

                metaParts.push(`第${index + 1}/${images.length}张`);

            }

            addCandidate(

                imageUrl,

                '镜头图候选',

                metaParts.join(' · '),

                'detail'

            );

        });

    });

    return candidates;

}



async function uploadFirstFrameReferenceImage() {

    if (!APP_STATE.currentShot) return;

    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';

    input.onchange = async (event) => {
        const file = event.target.files && event.target.files[0];
        if (!file || !APP_STATE.currentShot) return;

        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}/first-frame-reference-image`, {
                method: 'POST',
                body: formData
            });

            let result = null;
            try {
                result = await response.json();
            } catch (error) {
                result = null;
            }

            if (!response || !response.ok) {
                throw new Error(result?.detail || '上传首帧参考图失败');
            }

            updateShotInState(APP_STATE.currentShot.id, {
                uploaded_first_frame_reference_image_url: result?.uploaded_first_frame_reference_image_url || '',
                first_frame_reference_image_url: result?.first_frame_reference_image_url || APP_STATE.currentShot.first_frame_reference_image_url || ''
            });
            await renderShotFirstFrameReferenceGrid();
            showToast('首帧参考图已上传，请手动勾选需要使用的图片', 'success');
        } catch (error) {
            console.error('Failed to upload first-frame reference image:', error);
            showToast(`上传首帧参考图失败: ${error.message}`, 'error');
        }
    };

    input.click();

}


function hasGeneratingStoryboardPromptShots(shots = APP_STATE.shots) {

    if (!Array.isArray(shots) || shots.length === 0) {

        return false;

    }



    return shots.some(shot => shot && shot.sora_prompt_status === 'generating');

}


function hasGeneratingStoryboardReasoningPromptShots(shots = APP_STATE.shots) {

    if (!Array.isArray(shots) || shots.length === 0) {

        return false;

    }



    return shots.some(shot => shot && shot.reasoning_prompt_status === 'generating');

}



function isStoryboardPromptBatchGenerating() {

    if (APP_STATE.storyboardPromptBatchSubmitting) {

        return true;

    }



    if (Array.isArray(APP_STATE.shots) && APP_STATE.shots.length > 0) {

        return hasGeneratingStoryboardPromptShots(APP_STATE.shots);

    }



    return Boolean(APP_STATE.currentEpisodeInfo?.batch_generating_prompts);

}


function isStoryboardReasoningPromptBatchGenerating() {

    if (APP_STATE.storyboardReasoningPromptBatchSubmitting) {

        return true;

    }



    if (Array.isArray(APP_STATE.shots) && APP_STATE.shots.length > 0) {

        return hasGeneratingStoryboardReasoningPromptShots(APP_STATE.shots);

    }



    return false;

}



function reconcileLocalStoryboardPromptBatchFlag() {

    if (!APP_STATE.currentEpisodeInfo) {

        APP_STATE.currentEpisodeInfo = {};

    }



    if (APP_STATE.storyboardPromptBatchSubmitting) {

        APP_STATE.currentEpisodeInfo.batch_generating_prompts = true;

        return true;

    }



    if (Array.isArray(APP_STATE.shots) && APP_STATE.shots.length > 0) {

        APP_STATE.currentEpisodeInfo.batch_generating_prompts = hasGeneratingStoryboardPromptShots(APP_STATE.shots);

    }



    return Boolean(APP_STATE.currentEpisodeInfo.batch_generating_prompts);

}



function updateStoryboardBatchGeneratingUi() {

    const isGenerating = isStoryboardPromptBatchGenerating();

    const batchGenerateBtn = document.getElementById('batchGenerateBtn');

    if (batchGenerateBtn) {

        batchGenerateBtn.disabled = isGenerating;

    }



    const toolsRight = document.querySelector('.storyboard-tools-right');

    if (!toolsRight) {

        return;

    }



    let statusEl = toolsRight.querySelector('.batch-generate-status');

    if (isGenerating) {

        if (!statusEl) {

            statusEl = document.createElement('span');

            statusEl.className = 'batch-generate-status';

            statusEl.textContent = '正在批量生成中...';

            if (toolsRight.firstChild) {

                toolsRight.insertBefore(statusEl, toolsRight.firstChild);

            } else {

                toolsRight.appendChild(statusEl);

            }

        } else {

            statusEl.style.display = '';

        }

        return;

    }



    if (statusEl) {

        statusEl.remove();

    }

}



function buildOptimisticSimpleStoryboardBatchState() {

    return {

        generating: true,

        error: '',

        shotsCount: 0,

        totalBatches: 0,

        completedBatches: 0,

        failedBatches: 0,

        submittingBatches: 0,

        batches: [],

        failedBatchErrors: [],

        hasFailures: false,

    };

}



async function renderShotFirstFrameReferenceGrid() {

    const container = document.getElementById('shotFirstFrameGrid');

    if (!container) return;

    if (!APP_STATE.currentShot) {

        container.innerHTML = '<div class="storyboard-empty-state">请先选择镜头</div>';

        return;

    }

    try {

        const response = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}/detail-images`);

        if (!response || !response.ok) {

            container.innerHTML = '<div class="storyboard-empty-state">加载失败</div>';

            return;

        }

        const detailImagesPayload = await response.json();

        const candidates = buildShotFirstFrameReferenceCandidates(APP_STATE.currentShot, detailImagesPayload);

        const selectedImageUrl = String(APP_STATE.currentShot.first_frame_reference_image_url || '').trim();

        if (candidates.length === 0) {

            container.innerHTML = '<div class="storyboard-empty-state">暂无可选镜头图</div>';

            return;

        }

        container.innerHTML = candidates.map(candidate => {

            const encodedImageUrl = encodeURIComponent(candidate.image_url);

            const isSelected = selectedImageUrl === candidate.image_url;

            return `

                <div class="storyboard-collage-card-simple"

                     style="position: relative; aspect-ratio: 16/9; background: #0a0a0a; border: 1px solid ${isSelected ? '#4caf50' : '#2a2a2a'}; border-radius: 4px; overflow: hidden; cursor: pointer;"

                     onclick="toggleFirstFrameReferenceSelection('${encodedImageUrl}')">

                    <img src="${escapeHtml(candidate.image_url)}"

                         alt="${escapeHtml(candidate.title)}"

                         style="width: 100%; height: 100%; object-fit: contain; background: #111;">

                    <div style="position: absolute; top: 8px; right: 8px; z-index: 2; background: rgba(0,0,0,0.55); border-radius: 999px; padding: 2px;">

                        <input type="checkbox"

                               ${isSelected ? 'checked' : ''}

                               onclick="event.stopPropagation(); toggleFirstFrameReferenceSelection('${encodedImageUrl}')"

                               style="width: 18px; height: 18px; cursor: pointer;">

                    </div>

                    <div style="position: absolute; left: 0; right: 0; bottom: 0; padding: 8px; background: linear-gradient(180deg, rgba(0,0,0,0) 0%, rgba(0,0,0,0.82) 100%);">

                        <div style="color: #fff; font-size: 12px; font-weight: 500;">${escapeHtml(candidate.title)}</div>

                        <div style="color: #aaa; font-size: 11px; margin-top: 2px; min-height: 14px;">${escapeHtml(candidate.meta || '')}</div>

                    </div>

                </div>

            `;

        }).join('');

    } catch (error) {

        console.error('Failed to load first-frame references:', error);

        container.innerHTML = '<div class="storyboard-empty-state">加载失败</div>';

    }

}



async function toggleFirstFrameReferenceSelection(encodedImageUrl) {

    if (!APP_STATE.currentShot) return;

    const imageUrl = decodeURIComponent(String(encodedImageUrl || '')).trim();

    const currentSelectedUrl = String(APP_STATE.currentShot.first_frame_reference_image_url || '').trim();

    const nextImageUrl = currentSelectedUrl === imageUrl ? '' : imageUrl;

    try {

        const response = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}/first-frame-reference`, {

            method: 'PATCH',

            body: JSON.stringify({ image_url: nextImageUrl })

        });

        let result = null;

        try {

            result = await response.json();

        } catch (error) {

            result = null;

        }

        if (!response.ok) {

            throw new Error(result?.detail || '设置首帧参考图失败');

        }

        updateShotInState(APP_STATE.currentShot.id, {

            first_frame_reference_image_url: result?.first_frame_reference_image_url || ''

        });

        await renderShotFirstFrameReferenceGrid();

        showToast(nextImageUrl ? '已设置首帧参考图' : '已取消首帧参考图', 'success');

    } catch (error) {

        console.error('Failed to toggle first-frame reference selection:', error);

        showToast(`设置失败: ${error.message}`, 'error');

    }

}





async function nextStep() {

    if (APP_STATE.currentStep === 0) {

        const isNewScript = !APP_STATE.currentScript;



        if (isNewScript) {

            // 新建模式：创建剧鏈拰鐗囨

            const scriptSelector = document.getElementById('scriptSelector');

            const newScriptName = document.getElementById('newScriptName');

            const episodeName = document.getElementById('episodeName');

            const episodeContent = document.getElementById('episodeContent');



            let scriptId = scriptSelector.value;

            const selectedScriptId = scriptId;



            // 妫€查是新建还是选择已有剧本

            if (!scriptId) {

                // 新建剧本

                const scriptName = newScriptName.value.trim();

                if (!scriptName) {

                    alert('请输入剧本名称');

                    return;

                }



                // 获取风格模板鍐呭

                const styleTemplateContent = document.getElementById('styleTemplateContent')?.value.trim() || '';



                try {

                    const response = await apiRequest('/api/scripts', {

                        method: 'POST',

                        body: JSON.stringify({

                            name: scriptName,

                            style_template: styleTemplateContent

                        })

                    });



                    if (!response.ok) {

                        alert('创建剧本失败');

                        return;

                    }



                    const script = await response.json();

                    scriptId = script.id;

                } catch (error) {

                    console.error('Failed to create script:', error);

                    alert('创建剧本失败');

                    return;

                }

            }



            // 创建鐗囨

            const epName = episodeName.value.trim();

            const epContent = episodeContent.value.trim();

            const batchSize = document.getElementById('batchSize')?.value || 500;

            const duration = document.getElementById('storyboard2Duration')?.value || 15;



            if (!epName || !epContent) {

                alert('请填写片段名和文案');

                return;

            }



            try {

                const response = await apiRequest(`/api/scripts/${scriptId}/episodes`, {

                    method: 'POST',

                    body: JSON.stringify({

                        name: epName,

                        content: epContent,

                        batch_size: parseInt(batchSize),

                        storyboard2_duration: parseInt(duration)

                    })

                });



                if (!response.ok) {

                    alert('创建片段失败');

                    return;

                }



                const episode = await response.json();

                APP_STATE.currentScript = scriptId;

                APP_STATE.currentEpisode = episode.id;



                // 生成绠€单分闀?

                await generateSimpleStoryboardAndProceed();



            } catch (error) {

                console.error('Failed to create episode:', error);

                alert('创建片段失败');

            }



        } else {

            // 编辑模式：保存剧鏈暟鎹?

            const name = document.getElementById('episodeName').value;

            const content = document.getElementById('episodeContent').value;

            const duration = document.getElementById('storyboard2Duration')?.value || 15;

            const batchSize = document.getElementById('batchSize')?.value || 500;



            if (!name.trim() || !content.trim()) {

                alert('请填写片段名和文案');

                return;

            }



            // 鉁?妫€查是鍚︽在转鎹负瑙ｈ鍓?

            try {

                const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}`);

                if (response.ok) {

                    const episode = await response.json();

                    if (episode.narration_converting) {

                        alert('正在转换为解说剧，请等待转换完成后再进入下一步');

                        return;

                    }

                }

            } catch (error) {

                console.error('Failed to check narration status:', error);

            }



            try {

                // 获取风格模板鍐呭

                const styleTemplateContent = document.getElementById('styleTemplateContent')?.value.trim() || '';



                // 更新Script的style_template

                await apiRequest(`/api/scripts/${APP_STATE.currentScript}`, {

                    method: 'PUT',

                    body: JSON.stringify({

                        style_template: styleTemplateContent

                    })

                });



                // 更新Episode（包含batch_size锛?

                await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}`, {

                    method: 'PUT',

                    body: JSON.stringify({

                        name: name.trim(),

                        content: content.trim(),

                        batch_size: parseInt(batchSize)

                    })

                });



                // 更新时长规格

                await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/storyboard2-duration`, {

                    method: 'PUT',

                    body: JSON.stringify({

                        duration: parseInt(duration)

                    })

                });



                // 生成绠€单分闀?

                await generateSimpleStoryboardAndProceed();



            } catch (error) {

                console.error('Failed to save and analyze:', error);

                alert('保存或分析失败');

            }

        }

    } else if (APP_STATE.currentStep === 1) {

        // 从简单分闀?鈫?详细分镜

        const simpleBatchState = APP_STATE.simpleStoryboardBatchState;

        if (simpleBatchState?.hasFailures) {

            showToast('简单分镜存在失败批次，请重新生成整次简单分镜', 'error');

            return;

        }

        // 妫€查是否已鏈夎细分镜数鎹?

        try {

            const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/storyboard`);

            const data = await response.json();

            const hasDetailedStoryboard = data.shots && data.shots.length > 0;



            if (hasDetailedStoryboard) {

                // 如果已有详细分镜，显示确认弹绐?

                const confirmed = await showConfirmModal(

                    '该步骤会清空详细分镜并重新生成，是否确认？',

                    '确认'

                );

                if (!confirmed) return;

            }



            await generateDetailedStoryboardAndProceed();

        } catch (error) {

            console.error('Failed to check detailed storyboard:', error);

            // 如果妫€查失败，直接继续生成

            await generateDetailedStoryboardAndProceed();

        }

    } else if (APP_STATE.currentStep === 2) {

        // 浠庤细分闀?鈫?主体（先调用创建主体和镜头）

        const confirmed = await showConfirmModal(

            '该步骤会清空主体和镜头重新生成，是否确认？',

            '确认'

        );

        if (!confirmed) return;



        await createFromStoryboard();

    } else if (APP_STATE.currentStep === 3) {

        // 从主浣?鈫?故事鏉?

        await switchStep(4);

    } else if (APP_STATE.currentStep === 4) {

        // ?sora? -> ?2

        await switchStep(5);

    } else if (APP_STATE.currentStep === 5) {

        // ?2 -> ?

        await switchStep(6);

    } else if (APP_STATE.currentStep === 6) {

        // ? -> ?

        backToScript();

    }

}



// 鍚姩后台分镜表生成并跳转到分镜表界面（旧流程，暂时保留）

async function analyzeAndProceed() {

    // 直接跳转到分镜表界面，后台会鑷姩生成

    await switchStep(1);

}



// 新流程：生成绠€单分镜并跳转

async function generateSimpleStoryboardAndProceed() {

    try {

        // 读取当前的batch_size设置

        const batchSizeInput = document.getElementById('batchSize');

        const batchSize = batchSizeInput ? parseInt(batchSizeInput.value) || 500 : 500;



        APP_STATE.simpleStoryboardLoadVersion += 1;
        APP_STATE.simpleStoryboardSubmissionPending = true;

        APP_STATE.simpleStoryboardBatchState = buildOptimisticSimpleStoryboardBatchState();

        const responsePromise = apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/generate-simple-storyboard`, {

            method: 'POST',

            body: JSON.stringify({

                batch_size: batchSize

            })

        });

        await switchStep(1);

        showToast('简单分镜生成中...', 'info');

        const response = await responsePromise;

        const responseData = await response.json().catch(() => ({}));

        console.log('[SimpleStoryboard][POST /generate-simple-storyboard]', {

            episodeId: APP_STATE.currentEpisode,

            ok: Boolean(response.ok),

            status: Number(response.status || 0),

            submittedBatches: Number(responseData?.submitted_batches || 0),

            totalBatches: Number(responseData?.total_batches || 0),

            completedBatches: Number(responseData?.completed_batches || 0),

            failedBatches: Number(responseData?.failed_batches || 0),

            submittingBatches: Number(responseData?.submitting_batches || 0),

            shotsCount: Array.isArray(responseData?.shots) ? responseData.shots.length : 0,

            error: String(responseData?.error || responseData?.detail || '')

        });

        APP_STATE.simpleStoryboardSubmissionPending = false;

        APP_STATE.simpleStoryboardLoadVersion += 1;



        if (!response.ok) {

            throw new Error(responseData.detail || '生成简单分镜失败');

        }

        if (APP_STATE.currentStep === 1) {

            const hasRenderableShots = Array.isArray(responseData?.shots) && responseData.shots.length > 0;
            const shouldFallbackToFetch = !hasRenderableShots && Number(responseData?.submitted_batches || 0) > 0;

            if (hasRenderableShots || !shouldFallbackToFetch) {

                applySimpleStoryboardStepData(responseData, {

                    requestVersion: APP_STATE.simpleStoryboardLoadVersion

                });

            } else {

                console.warn('[SimpleStoryboard] POST response missing renderable shots, falling back to GET /simple-storyboard');

                await loadSimpleStoryboardStep({ forceRemote: true });

            }

            showToast('简单分镜生成完成！', 'success');

        }

    } catch (error) {

        APP_STATE.simpleStoryboardSubmissionPending = false;

        console.error('Failed to generate simple storyboard:', error);

        if (APP_STATE.currentStep === 1) {

            await loadSimpleStoryboardStep();

        }

        alert(error.message || '生成简单分镜失败');

    }

}



// 新流程：生成详细分镜并跳杞?

async function generateDetailedStoryboardAndProceed() {

    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/generate-detailed-storyboard`, {

            method: 'POST'

        });



        if (!response.ok) {

            const errorData = await response.json();

            throw new Error(errorData.detail || '生成详细分镜失败');

        }



        // 跳转鍒拌细分镜界闈?

        await switchStep(2);

        showToast('详细分镜生成中...', 'info');

    } catch (error) {

        console.error('Failed to generate detailed storyboard:', error);

        alert(error.message || '生成详细分镜失败');

    }

}



async function saveSoraPrompt() {

    if (!APP_STATE.currentShot) return;

    const soraPromptInput = document.getElementById('soraPrompt');

    const soraPrompt = soraPromptInput ? soraPromptInput.value.trim() : '';

    if (!soraPrompt) {

        showToast('请先填写Sora提示词', 'info');

        return;

    }



    try {

        const response = await apiRequest(`/api/shots/${APP_STATE.currentShot.id}`, {

            method: 'PUT',

            body: JSON.stringify({ sora_prompt: soraPrompt })

        });



        if (response && response.ok) {

            const updatedShot = await response.json();

            APP_STATE.currentShot = updatedShot;

            const index = APP_STATE.shots.findIndex(s => s.id === updatedShot.id);

            if (index >= 0) {

                APP_STATE.shots[index] = updatedShot;

            }

            showToast('Sora提示词已保存', 'success');

        } else if (response) {

            const error = await response.json();

            showToast(error.detail || '保存失败', 'error');

        }

    } catch (error) {

        console.error('Failed to save sora prompt:', error);

        showToast('保存失败', 'error');

    }

}



function prevStep() {

    if (APP_STATE.currentStep > 0) {

        switchStep(APP_STATE.currentStep - 1);

    }

}



function backToScript() {

    if (APP_STATE.currentScript) {

        openScript(APP_STATE.currentScript);

    } else {

        loadView('my-scripts');

    }

}



function toggleStoryboardMoreButtons(event) {

    event?.stopPropagation?.();

    const menu = document.getElementById('storyboardMoreMenu');

    if (!menu) return;

    cancelHideStoryboardMoreButtons();

    menu.classList.toggle('open');

}



function hideStoryboardMoreButtons() {

    const menu = document.getElementById('storyboardMoreMenu');

    if (!menu) return;

    menu.classList.remove('open');

}



function scheduleHideStoryboardMoreButtons() {

    cancelHideStoryboardMoreButtons();

    window.__storyboardMoreMenuHideTimer = window.setTimeout(() => {

        hideStoryboardMoreButtons();

    }, 160);

}



function cancelHideStoryboardMoreButtons() {

    if (!window.__storyboardMoreMenuHideTimer) return;

    window.clearTimeout(window.__storyboardMoreMenuHideTimer);

    window.__storyboardMoreMenuHideTimer = null;

}



if (!window.__storyboardMoreMenuBound) {

    document.addEventListener('click', (event) => {

        const menu = document.getElementById('storyboardMoreMenu');

        if (!menu || menu.contains(event.target)) return;

        hideStoryboardMoreButtons();

    });

    window.__storyboardMoreMenuBound = true;

}



function getFrontendPageUrl(path = '/') {
    const normalizedPath = String(path || '/').startsWith('/') ? String(path || '/') : `/${path}`;
    return `${window.location.origin}${normalizedPath}`;
}

function openPromptManagement() {
    window.open(getFrontendPageUrl('/manage'), '_blank');
}

function openAdminPanel() {
    window.open(getFrontendPageUrl('/admin'), '_blank');
}

function openModelSelectPanel() {
    window.open(getFrontendPageUrl('/model-select'), '_blank');
}

function openBillingPanel() {
    window.open(getFrontendPageUrl('/billing'), '_blank');
}

function openDashboardPanel() {
    window.open(getFrontendPageUrl('/dashboard'), '_blank');
}

function openJimengDashboard() {
    window.open('https://ne.mocatter.cn/video/dashboard', '_blank');
}



function backToScriptList() {

    if (APP_STATE.currentScript) {

        openScript(APP_STATE.currentScript);

    } else {

        loadView('my-scripts');

    }

}



// 辅助函数锛氬理图片URL锛圕DN链接或本地路径）

function getImageUrl(imagePath) {

    if (!imagePath) return '';

    if (imagePath.startsWith('http://') || imagePath.startsWith('https://')) {

        return imagePath; // CDN完整URL

    }

    return `/${imagePath}`; // 鏈湴璺緞（兼容旧数据锛?

}



// 图片妯℃€佹相关函数

function updateImageModal() {

    const { images, currentIndex } = APP_STATE.imageModal;



    if (images.length === 0) {

        closeImageModal();

        return;

    }



    const currentImage = images[currentIndex];



    document.getElementById('modalImage').src = getImageUrl(currentImage.image_path);

    document.getElementById('imageCounter').textContent = `${currentIndex + 1} / ${images.length}`;



    document.getElementById('prevImage').disabled = currentIndex === 0;

    document.getElementById('nextImage').disabled = currentIndex === images.length - 1;

}



function navigateImage(direction) {

    const { images, currentIndex } = APP_STATE.imageModal;



    let newIndex = currentIndex + direction;



    if (newIndex < 0) newIndex = 0;

    if (newIndex >= images.length) newIndex = images.length - 1;



    APP_STATE.imageModal.currentIndex = newIndex;

    updateImageModal();

}



function downloadCurrentImage() {

    const { images, currentIndex } = APP_STATE.imageModal;

    const currentImage = images[currentIndex];



    const link = document.createElement('a');

    link.href = getImageUrl(currentImage.image_path);

    link.download = currentImage.image_path.split('/').pop();

    link.click();

}



async function deleteCurrentImage() {

    const confirmed = await showConfirmModal('确定要删除这张图片吗？');

    if (!confirmed) return;



    const { images, currentIndex, cardId } = APP_STATE.imageModal;

    const currentImage = images[currentIndex];



    try {

        const response = await apiRequest(`/api/images/${currentImage.id}`, {

            method: 'DELETE'

        });



        if (response.ok) {

            images.splice(currentIndex, 1);



            if (images.length === 0) {

                closeImageModal();

                await reloadSubjectStepPreserveState();

            } else {

                if (APP_STATE.imageModal.currentIndex >= images.length) {

                    APP_STATE.imageModal.currentIndex = images.length - 1;

                }

                updateImageModal();

            }

        } else if (response.status === 400) {

            // 显示鍚庣返回的错璇俊鎭紙如：不能删除鏈€后一张主体素材图锛?

            const error = await response.json();

            showToast(error.detail || '删除失败', 'error');

        } else {

            showToast('删除失败', 'error');

        }

    } catch (error) {

        console.error('Failed to delete image:', error);

        showToast('删除失败', 'error');

    }

}



function closeImageModal() {

    document.getElementById('imageModal').classList.remove('active');

    APP_STATE.imageModal.isOpen = false;

}



// 鍏紑娴忚相关函数（保留原鏈夐€昏緫锛?

async function loadPublicLibraries() {

    const content = document.getElementById('content');



    content.innerHTML = `

        <div class="page-header">

            <h2 class="page-title">用户列表</h2>

            <p class="page-subtitle">浏览其他用户的作品</p>

        </div>

        <div class="users-grid" id="usersGrid">

            <div class="loading">加载中...</div>

        </div>

    `;



    try {

        const response = await apiRequest('/api/public/users');

        const users = await response.json();



        const grid = document.getElementById('usersGrid');



        if (users.length === 0) {

            grid.innerHTML = '<div class="empty-state"><div class="empty-state-text">暂无用户</div></div>';

        } else {

            grid.innerHTML = users.map(user => `

                <div class="user-card" onclick="viewUserScripts(${user.id}, '${escapeHtml(user.username)}')">

                    <div class="user-icon">U</div>

                    <div class="user-name">${escapeHtml(user.username)}</div>

                    <div class="user-stats">

                        ${user.library_count} 涓綔鍝?

                    </div>

                </div>

            `).join('');

        }

    } catch (error) {

        console.error('Failed to load users:', error);

        document.getElementById('usersGrid').innerHTML = '<div class="empty-state">加载失败</div>';

    }

}



async function viewUserScripts(userId, username) {

    // TODO: 实现查看用户剧本列表

    alert('查看用户剧本功能待实现');

}



// 工具函数

function escapeHtml(text) {

    const div = document.createElement('div');

    div.textContent = text;

    return div.innerHTML;

}



// 生成UUID（用于镜头的stable_id锛?

function generateUUID() {

    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {

        const r = Math.random() * 16 | 0;

        const v = c === 'x' ? r : (r & 0x3 | 0x8);

        return v.toString(16);

    });

}



function closeLibraryFormModal() {

    document.getElementById('libraryFormModal').classList.remove('active');

}



function closeConfirmModal() {

    document.getElementById('confirmModal').classList.remove('active');

    APP_STATE.confirmCallback = null;

}



// 显示输入妯℃€佹（替浠rompt锛?

function showInputModal(title, label, defaultValue = '', placeholder = '') {

    return new Promise((resolve) => {

        const modal = document.getElementById('inputModal');

        const titleEl = document.getElementById('inputModalTitle');

        const labelEl = document.getElementById('inputModalLabel');

        const input = document.getElementById('inputModalValue');

        const confirmBtn = document.getElementById('inputModalConfirm');



        titleEl.textContent = title;

        labelEl.textContent = label;

        input.value = defaultValue;

        input.placeholder = placeholder;



        // 移除旧的事件监听鍣?

        const newConfirmBtn = confirmBtn.cloneNode(true);

        confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);



        newConfirmBtn.addEventListener('click', () => {

            const value = input.value.trim();

            modal.classList.remove('active');

            resolve(value);

        });



        // ESC閿叧闂?

        const escHandler = (e) => {

            if (e.key === 'Escape') {

                modal.classList.remove('active');

                document.removeEventListener('keydown', escHandler);

                resolve(null);

            }

        };

        document.addEventListener('keydown', escHandler);



        modal.classList.add('active');

        setTimeout(() => input.focus(), 100);

    });

}



function closeInputModal() {

    document.getElementById('inputModal').classList.remove('active');

}



function showTextareaModal(title, label, defaultValue = '', placeholder = '') {

    return new Promise((resolve) => {

        const modal = document.getElementById('textareaModal');

        const titleEl = document.getElementById('textareaModalTitle');

        const labelEl = document.getElementById('textareaModalLabel');

        const input = document.getElementById('textareaModalValue');

        const confirmBtn = document.getElementById('textareaModalConfirm');



        titleEl.textContent = title;

        labelEl.textContent = label;

        input.value = defaultValue;

        input.placeholder = placeholder;



        const newConfirmBtn = confirmBtn.cloneNode(true);

        confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);



        newConfirmBtn.addEventListener('click', () => {

            const value = input.value.trim();

            modal.classList.remove('active');

            resolve(value);

        });



        const cancelHandler = () => {

            modal.classList.remove('active');

            resolve(null);

        };



        const escHandler = (e) => {

            if (e.key === 'Escape') {

                cancelHandler();

                document.removeEventListener('keydown', escHandler);

            }

        };

        document.addEventListener('keydown', escHandler);



        modal.classList.add('active');

        setTimeout(() => input.focus(), 100);

    });

}



function closeTextareaModal() {

    document.getElementById('textareaModal').classList.remove('active');

}



// 显示纭妯℃€佹（替浠onfirm锛?

function showConfirmModal(message, title = '确认') {

    return new Promise((resolve) => {

        const modal = document.getElementById('confirmModal');

        const titleEl = document.getElementById('confirmTitle');

        const messageEl = document.getElementById('confirmMessage');

        const confirmBtn = document.getElementById('confirmButton');



        titleEl.textContent = title;

        messageEl.textContent = message;



        // 移除旧的事件监听鍣?

        const newConfirmBtn = confirmBtn.cloneNode(true);

        confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);



        newConfirmBtn.addEventListener('click', () => {

            modal.classList.remove('active');

            resolve(true);

        });



        // 点击取消或ESC

        const cancelHandler = () => {

            modal.classList.remove('active');

            resolve(false);

        };



        const escHandler = (e) => {

            if (e.key === 'Escape') {

                cancelHandler();

                document.removeEventListener('keydown', escHandler);

            }

        };

        document.addEventListener('keydown', escHandler);



        modal.classList.add('active');

    });

}



// ==================== 瑙嗛鐘舵€佽疆询和导出 ====================



// 鍚姩瑙嗛鐘舵€佽疆璇?

function startVideoStatusPolling() {

    console.log('[杞DEBUG] startVideoStatusPolling 琚皟鐢?');



    // 如果已有杞，先鍋滄

    if (APP_STATE.videoPollingInterval) {

        clearInterval(APP_STATE.videoPollingInterval);

    }



    // 姣?绉掓查一娆?

    APP_STATE.videoPollingInterval = setInterval(async () => {

        await checkAllVideoStatus();

    }, VIDEO_STATUS_POLL_INTERVAL_MS);



    // 立即鎵ц涓€娆?

    checkAllVideoStatus();

}



// 鍋滄瑙嗛鐘舵€佽疆璇?

function stopVideoStatusPolling() {

    if (APP_STATE.videoPollingInterval) {

        clearInterval(APP_STATE.videoPollingInterval);

        APP_STATE.videoPollingInterval = null;

    }

}



// 妫€查所有镜头的瑙嗛鐘舵€?

async function checkAllVideoStatus() {

    if (!APP_STATE.currentEpisode) {

        return;

    }



    return withPollingGuard('videoStatus', async () => {

        try {

            const episodeInfo = await fetchEpisodePollStatus(APP_STATE.currentEpisode);

            APP_STATE.currentEpisodeInfo = {

                ...(APP_STATE.currentEpisodeInfo || {}),

                ...episodeInfo

            };



            const shotsUrl = `/api/episodes/${APP_STATE.currentEpisode}/shots`;

            const response = await apiRequest(shotsUrl);

            const shots = await response.json();



        // 妫€测任务完成：比较当前鐘舵€佸拰之前鐘舵€?

        const currentProcessingTasks = new Set();

        let soraPromptJustCompleted = false; // 鏍囪鏄惁有Sora提示词刚完成

        let shouldRefreshFirstFrameReferences = false; // 标记是否需要刷新首帧参考图候选



        shots.forEach(shot => {

            if (shot.video_status === 'processing' || shot.video_status === 'submitting' || shot.video_status === 'preparing') {

                currentProcessingTasks.add(`${shot.id}:video`);

            } else if (shot.video_status === 'completed') {

                // 如果之前是processing，现在completed锛岃明完成了

                const taskKey = `${shot.id}:video`;

                if (APP_STATE.previousProcessingTasks.has(taskKey)) {

                    showToast(`镜头 #${shot.shot_number} 视频生成完成`, 'success');

                    APP_STATE.previousProcessingTasks.delete(taskKey);

                }

            }



            const previousShot = APP_STATE.shots.find(s => s.id === shot.id);

            if (APP_STATE.currentShot && shot.id === APP_STATE.currentShot.id) {

                if (!previousShot
                    || previousShot.storyboard_image_path !== shot.storyboard_image_path
                    || previousShot.detail_images_status !== shot.detail_images_status
                    || previousShot.first_frame_reference_image_url !== shot.first_frame_reference_image_url) {

                    shouldRefreshFirstFrameReferences = true;

                }

            }



            // Sora提示词生成任鍔?

            if (shot.sora_prompt_status === 'generating') {

                currentProcessingTasks.add(`${shot.id}:prompt`);

            } else if (shot.sora_prompt_status === 'completed' || shot.sora_prompt_status === 'failed') {

                // 如果之前是generating，现在是完成或失败，说明结束浜?

                const taskKey = `${shot.id}:prompt`;

                if (APP_STATE.previousProcessingTasks.has(taskKey)) {

                    if (shot.sora_prompt_status === 'completed') {

                        showToast(`镜头 #${shot.shot_number} Sora提示词生成完成`, 'success');

                        // 如果鏄綋鍓嶉€変腑的镜头，鏍囪闇€要重新渲染右侧栏

                        if (APP_STATE.currentShot && shot.id === APP_STATE.currentShot.id) {

                            soraPromptJustCompleted = true;

                        }

                    } else if (shot.sora_prompt_status === 'failed') {

                        showToast(`镜头 #${shot.shot_number} Sora提示词生成失败`, 'error');

                    }

                    APP_STATE.previousProcessingTasks.delete(taskKey);

                }

            }



            if (shot.reasoning_prompt_status === 'generating') {

                currentProcessingTasks.add(`${shot.id}:reasoning_prompt`);

            } else if (shot.reasoning_prompt_status === 'completed' || shot.reasoning_prompt_status === 'failed') {

                const taskKey = `${shot.id}:reasoning_prompt`;

                if (APP_STATE.previousProcessingTasks.has(taskKey)) {

                    if (shot.reasoning_prompt_status === 'completed') {

                        showToast(`镜头 #${shot.shot_number} 推理提示词生成完成`, 'success');

                        if (APP_STATE.currentShot && shot.id === APP_STATE.currentShot.id) {

                            soraPromptJustCompleted = true;

                        }

                    } else if (shot.reasoning_prompt_status === 'failed') {

                        showToast(`镜头 #${shot.shot_number} 推理提示词生成失败`, 'error');

                    }

                    APP_STATE.previousProcessingTasks.delete(taskKey);

                }

            }



            // 分镜图生成任务（废弃锛? 

            if (shot.storyboard_image_status === 'processing') {

                currentProcessingTasks.add(`${shot.id}:storyboard_image`);

            } else if (shot.storyboard_image_status === 'completed' || shot.storyboard_image_status === 'failed') {

                // 如果之前是processing，现在是完成或失败，说明结束浜?

                const taskKey = `${shot.id}:storyboard_image`;

                if (APP_STATE.previousProcessingTasks.has(taskKey)) {

                    if (shot.storyboard_image_status === 'completed') {

                        console.log(`[分镜图完成] 镜头 ${shot.id} (#${shot.shot_number})`, {

                            status: shot.storyboard_image_status,

                            path: shot.storyboard_image_path,

                            task_id: shot.storyboard_image_task_id

                        });

                        showToast(`镜头 #${shot.shot_number} 分镜图生成完成`, 'success');

                    } else if (shot.storyboard_image_status === 'failed') {

                        showToast(`镜头 #${shot.shot_number} 分镜图生成失败`, 'error');

                    }

                    APP_STATE.previousProcessingTasks.delete(taskKey);

                }

            }



            // 镜头图生成任务（新）

            if (shot.detail_images_status === 'processing') {

                currentProcessingTasks.add(`${shot.id}:detail_images`);

            } else if (shot.detail_images_status === 'completed' || shot.detail_images_status === 'failed') {

                // 如果之前是processing，现在是完成或失败，说明结束浜?

                const taskKey = `${shot.id}:detail_images`;

                if (APP_STATE.previousProcessingTasks.has(taskKey)) {

                    if (shot.detail_images_status === 'completed') {

                        console.log(`[镜头图完成] 镜头 ${shot.id} (#${shot.shot_number})`, {

                            status: shot.detail_images_status

                        });

                        showToast(`镜头 #${shot.shot_number} 镜头图生成完成`, 'success');

                    } else if (shot.detail_images_status === 'failed') {

                        showToast(`镜头 #${shot.shot_number} 镜头图生成失败`, 'error');

                    }

                    APP_STATE.previousProcessingTasks.delete(taskKey);

                }

            }

        });



        // 更新当前正在处理的任鍔?

        APP_STATE.previousProcessingTasks = currentProcessingTasks;



        // 更新APP_STATE涓殑shots

        APP_STATE.shots = shots;
        reconcileLocalStoryboardPromptBatchFlag();



        // 更新当前镜头

        if (APP_STATE.currentShot) {

            const updatedShot = shots.find(s => s.id === APP_STATE.currentShot.id);

            if (updatedShot) {

                APP_STATE.currentShot = updatedShot;

            }

        }



        // 使用增量更新刷新界面（不会中鏂敤户操作）

        renderStoryboardShotsGrid(); // 不传forceRebuild，默璁ゅ量更鏂?



        // 如果需要刷新首帧参考图候选

        if (shouldRefreshFirstFrameReferences) {

            await renderShotFirstFrameReferenceGrid();

        }



        // 如果Sora提示词刚完成，需要完全重新渲染右侧栏以展寮€鎵€有字娈?

        if (soraPromptJustCompleted) {

            renderStoryboardSidebar();

        } else {

            // 否则鍙仛增量更新

            updateSoraPromptTextarea(); // 鍙洿新Sora提示词textarea，不重建整个sidebar

            updateVideoGenerationButton(); // 更新瑙嗛生成按钮鐘舵€?

            updateSidebarTitleStatus(); // 更新鏍囬涓殑鐘舵€乥adge和task_id

        }



        // 鉁?更新批量生成按钮鐘舵€?

        const batchGenerateBtn = document.getElementById('batchGenerateBtn');

        const storyboardBatchGenerating = isStoryboardPromptBatchGenerating();

        const storyboard2BatchGenerating = APP_STATE.currentEpisodeInfo?.batch_generating_storyboard2_prompts || false;

        if (batchGenerateBtn) {

            const isGenerating = APP_STATE.currentStep === 5

                ? storyboard2BatchGenerating

                : storyboardBatchGenerating;

            batchGenerateBtn.disabled = isGenerating;

        }

        updateStoryboardBatchGeneratingUi();



        if (APP_STATE.currentStep === 5 && (storyboard2BatchGenerating || APP_STATE.storyboard2BatchGenerating)) {

            try {

                await refreshStoryboard2BoardDataAndRender();

            } catch (error) {

                console.error('Failed to refresh storyboard2 during polling:', error);

            }

        }

        APP_STATE.storyboard2BatchGenerating = storyboard2BatchGenerating;



        // 妫€查是否还鏈夊理中的任务（瑙嗛銆丼ora提示璇嶃€佸垎镜图、镜头图锛?

        const hasProcessing = storyboardBatchGenerating || storyboard2BatchGenerating || shots.some(s => {

            if (s.video_status === 'processing' || s.video_status === 'submitting' || s.video_status === 'preparing') {

                return true;

            }

            // Sora提示璇?

            if (s.sora_prompt_status === 'generating') {

                return true;

            }

            // 分镜图（废弃锛?

            if (s.storyboard_image_status === 'processing') {

                return true;

            }

            // 镜头图（新）

            if (s.detail_images_status === 'processing') {

                return true;

            }

            return false;

        });



            if (!hasProcessing) {

                // 没有处理中的任务，停止轮询

                console.log('[杞DEBUG] 没有处理涓换务，鍋滄杞');

                stopVideoStatusPolling();

            } else {

                console.log('[杞DEBUG] 还有处理涓换务，继续杞');

            }



            console.log('[杞DEBUG] checkAllVideoStatus 完成');



        } catch (error) {

            console.error('[杞DEBUG] checkAllVideoStatus 发生閿欒:', error);

            console.error('Failed to check video status:', error);

        }

    });

}



// ==================== 图片生成鐘舵€佽疆璇?====================



// 鍚姩图片生成鐘舵€佽疆璇?

function startImageStatusPolling() {

    // 如果已有杞，先鍋滄

    if (APP_STATE.imagePollingInterval) {

        clearInterval(APP_STATE.imagePollingInterval);

    }



    // 姣?0绉掓查一次（图片生成鐩稿较慢锛?

    APP_STATE.imagePollingInterval = setInterval(async () => {

        await checkImageGenerationStatus();

    }, IMAGE_STATUS_POLL_INTERVAL_MS);



    // 立即鎵ц涓€娆?

    checkImageGenerationStatus();

}


function getCardIdsNeedingImagePolling(state = APP_STATE) {

    const cardIds = [];

    const seen = new Set();

    const addCardId = (rawId) => {

        const cardId = Number.parseInt(rawId, 10);

        if (!Number.isFinite(cardId) || cardId <= 0 || seen.has(cardId)) {

            return;

        }

        seen.add(cardId);

        cardIds.push(cardId);

    };

    addCardId(state?.selectedCardForPrompt);

    const cards = Array.isArray(state?.cards) ? state.cards : [];

    cards.forEach(card => {

        if (!card) {

            return;

        }

        const generatingCount = Number.parseInt(card.generating_count, 10) || 0;

        if (card.is_generating_images || generatingCount > 0) {

            addCardId(card.id);

        }

    });

    return cardIds;

}



// 鍋滄图片生成鐘舵€佽疆璇?

function stopImageStatusPolling() {

    if (APP_STATE.imagePollingInterval) {

        clearInterval(APP_STATE.imagePollingInterval);

        APP_STATE.imagePollingInterval = null;

    }

}



// 妫€查当前卡片的图片生成鐘舵€?

async function checkImageGenerationStatus() {

    const cardIds = getCardIdsNeedingImagePolling(APP_STATE);

    if (!cardIds.length) {

        stopImageStatusPolling();

        return;

    }



    return withPollingGuard('imageStatus', async () => {

        try {

            let hasAnyProcessing = false;

            for (const cardId of cardIds) {

                const response = await apiRequest(`/api/cards/${cardId}/generated-images`);

                if (!response.ok) {

                    continue;

                }

                const images = await response.json();

                const hasProcessing = images.some(img => img.status === 'processing');

                if (hasProcessing) {

                    hasAnyProcessing = true;

                }

                const normalizedImages = await ensureSubjectReferenceImage(cardId, images);

                const card = APP_STATE.cards.find(c => c.id === cardId);

                if (card) {

                    card.generated_images = normalizedImages;

                    card.is_generating_images = hasProcessing;

                    if (!hasProcessing) {

                        card.generating_count = 0;

                    }

                }

                if (APP_STATE.selectedCardForPrompt === cardId && (hasProcessing || normalizedImages.length > 0)) {

                    await loadGeneratedImages(cardId, normalizedImages);

                }

                if (normalizedImages.length > 0) {

                    syncSubjectCardPreview(cardId, normalizedImages);

                }

            }

            if (!hasAnyProcessing) {

                stopImageStatusPolling();

            }

        } catch (error) {

            console.error('Failed to check image generation status:', error);

        }

    });

}



async function ensureSubjectReferenceImage(cardId, images) {

    if (!Array.isArray(images) || images.length === 0) {

        return [];

    }

    const hasReference = images.some(img => img?.status === 'completed' && img?.is_reference);

    if (hasReference) {

        return images;

    }



    const latestCompleted = images.find(img => img?.status === 'completed' && img?.image_path);

    if (!latestCompleted) {

        return images;

    }



    try {

        const response = await apiRequest(`/api/cards/${cardId}/reference-images`, {

            method: 'PUT',

            body: JSON.stringify({

                generated_image_ids: [latestCompleted.id]

            })

        });



        if (response.ok) {

            return images.map(img => ({

                ...img,

                is_reference: img.id === latestCompleted.id

            }));

        }

    } catch (error) {

        console.error('Failed to auto-set reference image:', error);

    }



    return images;

}



function syncSubjectCardPreview(cardId, images) {

    if (!Array.isArray(images) || images.length === 0) {

        return;

    }



    const card = APP_STATE.cards.find(c => c.id === cardId);

    if (card) {

        card.generated_images = images;

    }



    const cardElement = document.querySelector(`.subject-card[data-card-id="${cardId}"]`);

    if (!cardElement) {

        return;

    }



    const imageContainer = cardElement.querySelector('.card-image-container');

    if (!imageContainer) {

        return;

    }



    const existingImg = imageContainer.querySelector('.card-image');

    const placeholder = imageContainer.querySelector('.card-image-placeholder');

    const previewImage = card ? getCardPreviewImage(card) : '';



    if (previewImage) {

        if (existingImg) {

            existingImg.src = getImageUrl(previewImage);

        } else if (placeholder) {

            const cardName = card ? card.name : '';

            placeholder.outerHTML = `<img class="card-image" src="${getImageUrl(previewImage)}" alt="${escapeHtml(cardName)}">`;

        }

    } else if (existingImg) {

        existingImg.outerHTML = '<div class="card-image-placeholder">NO IMAGE</div>';

    }



    const expandButton = imageContainer.querySelector('.card-expand-button');

    if (previewImage && !expandButton) {

        const button = document.createElement('button');

        button.className = 'card-expand-button';

        button.onclick = function(e) {

            e.stopPropagation();

            openSubjectImageModal(cardId);

        };

        button.textContent = '预览';

        imageContainer.appendChild(button);

    } else if (!previewImage && expandButton) {

        expandButton.remove();

    }

}



// ==================== 服务商统计数鎹疆璇?====================



// 获取服务商统计数鎹?

async function fetchProvidersStats() {

    try {

        const response = await apiRequest('/api/video/providers/stats');



        if (!response.ok) {

            console.error('Failed to fetch providers stats:', response.status);

            return;

        }



        const data = await response.json();



        // 将数组转鎹负对象，以provider为key

        if (data.providers && Array.isArray(data.providers)) {

            const statsMap = {};

            data.providers.forEach(provider => {

                statsMap[provider.provider] = provider;

            });

            APP_STATE.providersStats = statsMap;



            // 如果当前在故事板页面，更新服务商选择器显绀?

            if (APP_STATE.currentView === 'storyboard' && APP_STATE.currentShot) {

                updateProviderSelectDisplay();

            }

        }

    } catch (error) {

        console.error('Failed to fetch providers stats:', error);

    }

}



// 更新服务鍟嗛€夋嫨器的显示（不重新渲染整个sidebar锛?

function updateProviderSelectDisplay() {

    const providerSelect = document.getElementById('providerSelect');

    if (!providerSelect) return;



    const currentValue = providerSelect.value;



    // 重新生成选项

    const providers = ['apimart', 'suchuang', 'yijia'];

    providerSelect.innerHTML = providers.map(provider => {

        const stats = APP_STATE.providersStats[provider];

        let displayText = provider;



        if (stats) {

            const successRate = stats.success_rate ? Math.round(stats.success_rate) : 0;

            const avgDuration = stats.average_duration ? Math.round(stats.average_duration) : null;



            if (avgDuration !== null) {

                displayText = `${provider} ${successRate}% ${avgDuration}s`;

            } else {

                displayText = `${provider} ${successRate}%`;

            }

        }



        const isSelected = provider === currentValue;

        return `<option value="${provider}" ${isSelected ? 'selected' : ''}>${displayText}</option>`;

    }).join('');

}



// 鍚姩服务商统计数鎹疆璇?

function startProvidersStatsPolling() {

    // 如果已有杞，先鍋滄

    if (APP_STATE.providersStatsPollingInterval) {

        clearInterval(APP_STATE.providersStatsPollingInterval);

    }



    // 姣?0绉掓查一娆?

    APP_STATE.providersStatsPollingInterval = setInterval(async () => {

        await fetchProvidersStats();

    }, 60000);



    // 立即鎵ц涓€娆?

    fetchProvidersStats();

}



// 鍋滄服务商统计数鎹疆璇?

function stopProvidersStatsPolling() {

    if (APP_STATE.providersStatsPollingInterval) {

        clearInterval(APP_STATE.providersStatsPollingInterval);

        APP_STATE.providersStatsPollingInterval = null;

    }

}



// 导出单个瑙嗛

async function exportVideo(shotId) {

    try {

        // 找到镜头信息获取shot_number和variant_index

        const shot = APP_STATE.shots && APP_STATE.shots.find(s => s.id === shotId);

        if (!shot) {

            showToast('找不到镜头信息', 'error');

            return;

        }



        const response = await apiRequest(`/api/shots/${shotId}/export`);

        if (response.ok) {

            const result = await response.json();



            // 下载视频而不是打开

            await downloadVideo(result.video_url, shot.shot_number, shot.variant_index);

            showToast(`成功下载镜头 ${shot.shot_number} 的视频`, 'success');

        } else {

            const error = await response.json();

            showToast(`导出失败: ${error.detail}`, 'error');

        }

    } catch (error) {

        console.error('Failed to export video:', error);

        showToast('导出失败', 'error');

    }

}



// }



// 涓€閿出所鏈夎棰?

async function exportAllVideos() {

    if (!APP_STATE.currentEpisode) return;



    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/export-all`);

        if (response.ok) {

            const result = await response.json();



            if (result.total_videos === 0) {

                alert('没有已完成的视频可导出');

                return;

            }



            // 显示导出列表

            let message = `共有 ${result.total_videos} 个视频可导出：\n\n`;

            result.videos.forEach(v => {

                message += `镜头 #${v.shot_number}\n`;

            });

            message += '\n点击确认后将在新标签页中打开所有视频';



            const confirmed = await showConfirmModal(message, '导出所有视频');

            if (confirmed) {

                // 逐个打开视频URL

                result.videos.forEach((v, index) => {

                    setTimeout(() => {

                        window.open(v.video_url, '_blank');

                    }, index * 500); // 每个间隔500ms，避免浏览器拦截

                });

            }

        } else {

            const error = await response.json();

            alert(`导出失败: ${error.detail}`);

        }

    } catch (error) {

        console.error('Failed to export all videos:', error);

        alert('导出失败');

    }

}



// ==================== AI作图功能 ====================



function normalizeImageModelCatalogResponse(data) {

    const catalog = { providers: [] };

    const providerMap = {};

    function normalizeText(value) {

        return String(value || '').trim();

    }

    function isBlockedImageModel(provider, modelId, modelName) {

        const providerText = normalizeText(provider).toLowerCase();

        const modelText = normalizeText(modelId).toLowerCase();

        const nameText = normalizeText(modelName).toLowerCase();

        return providerText === 'midjourney'
            || modelText === 'midjourney'
            || nameText.includes('midjourney')
            || modelText === 'gpt-image-1.5'
            || modelText === 'gpt image 1.5'
            || nameText === 'gpt-image-1.5'
            || nameText === 'gpt image 1.5';

    }

    function addModel(rawModel, fallbackModelId, fallbackProvider) {

        if (!rawModel || typeof rawModel !== 'object') {

            return;

        }

        const modelId = normalizeText(
            rawModel.value
            || rawModel.id
            || rawModel.key
            || rawModel.model
            || rawModel.model_name
            || rawModel.name_id
            || fallbackModelId
        );

        if (!modelId) {

            return;

        }

        const provider = normalizeText(
            rawModel.provider
            || rawModel.provider_id
            || rawModel.vendor
            || rawModel.route
            || fallbackProvider
            || 'default'
        );

        const modelName = normalizeText(rawModel.label || rawModel.display_name || rawModel.name || modelId);

        if (isBlockedImageModel(provider, modelId, modelName)) {

            return;

        }

        const providerLabel = normalizeText(
            rawModel.provider_label
            || rawModel.provider_name
            || rawModel.vendor_label
            || provider
        );

        if (!providerMap[provider]) {

            providerMap[provider] = {

                value: provider,

                label: providerLabel,

                models: []

            };

            catalog.providers.push(providerMap[provider]);

        }

        const sizes = Array.isArray(rawModel.sizes)
            ? rawModel.sizes
            : (Array.isArray(rawModel.ratios)
                ? rawModel.ratios
                : (Array.isArray(rawModel.aspect_ratios) ? rawModel.aspect_ratios : []));

        const resolutions = Array.isArray(rawModel.resolutions) ? rawModel.resolutions : [];

        providerMap[provider].models.push({

            value: modelId,

            label: modelName,

            sizes: sizes.map(item => normalizeText(item)).filter(Boolean),

            resolutions: resolutions.map(item => normalizeText(item)).filter(Boolean),

            supports_reference: Boolean(rawModel.supports_reference)

        });

    }

    const models = data && data.models;

    if (Array.isArray(models)) {

        models.forEach(item => {

            if (item && Array.isArray(item.models)) {

                const provider = normalizeText(item.provider || item.provider_id || item.vendor || item.route || item.value);

                item.models.forEach(model => addModel({

                    ...model,

                    provider: model.provider || provider,

                    provider_label: model.provider_label || item.provider_label || item.provider_name || item.label || item.name

                }, null, provider));

            } else if (item && Array.isArray(item.providers)) {

                const modelValue = normalizeText(item.value || item.id || item.key || item.model);

                const modelLabel = normalizeText(item.label || item.display_name || item.name || item.model || modelValue);

                item.providers.forEach(route => {

                    if (!route || route.enabled === false) {

                        return;

                    }

                    addModel({

                        ...item,

                        ...route,

                        value: modelValue,

                        label: modelLabel,

                        provider: route.provider || item.default_provider,

                        provider_label: route.provider_label || route.provider_name || route.provider || item.provider_label || item.provider_name,

                        ratios: Array.isArray(route.ratios) ? route.ratios : item.ratios,

                        resolutions: Array.isArray(route.resolutions) ? route.resolutions : item.resolutions,

                        supports_reference: route.supports_reference !== undefined ? route.supports_reference : item.supports_reference

                    }, modelValue, route.provider || item.default_provider);

                });

            } else {

                addModel(item);

            }

        });

    } else if (models && typeof models === 'object') {

        Object.entries(models).forEach(([modelId, modelInfo]) => {

            addModel(modelInfo, modelId);

        });

    }

    return catalog;

}



function getImageProviders(catalog) {

    if (!catalog || !Array.isArray(catalog.providers)) {

        return [];

    }

    return catalog.providers
        .filter(provider => provider && provider.value && Array.isArray(provider.models) && provider.models.length > 0)
        .map(provider => ({

            value: provider.value,

            label: provider.label || provider.value

        }));

}



function getImageModelsForProvider(catalog, provider) {

    if (!catalog || !Array.isArray(catalog.providers)) {

        return [];

    }

    const target = catalog.providers.find(item => item && item.value === provider);

    if (!target || !Array.isArray(target.models)) {

        return [];

    }

    return target.models.map(model => ({

        value: model.value,

        label: model.label || model.value

    }));

}



function getImageRouteOptions(catalog, provider, model) {

    const emptyOptions = {

        sizes: [],

        resolutions: [],

        supports_reference: false

    };

    if (!catalog || !Array.isArray(catalog.providers)) {

        return emptyOptions;

    }

    const targetProvider = catalog.providers.find(item => item && item.value === provider);

    const targetModel = targetProvider?.models?.find(item => item && item.value === model);

    if (!targetModel) {

        return emptyOptions;

    }

    return {

        sizes: Array.isArray(targetModel.sizes) ? targetModel.sizes.slice() : [],

        resolutions: Array.isArray(targetModel.resolutions) ? targetModel.resolutions.slice() : [],

        supports_reference: Boolean(targetModel.supports_reference)

    };

}



function getDefaultImageSelection(catalog, preferredProvider, preferredModel) {

    const providers = getImageProviders(catalog);

    const defaultModelPriority = ['seedream-4.0', 'jimeng-4.0'];

    if (providers.length === 0) {

        return {

            provider: null,

            model: null,

            size: '1:1',

            resolution: null

        };

    }

    let provider = preferredProvider && providers.some(item => item.value === preferredProvider)
        ? preferredProvider
        : null;

    if (!provider && preferredModel) {

        const matchedProvider = providers.find(item => {

            return getImageModelsForProvider(catalog, item.value).some(model => model.value === preferredModel);

        });

        provider = matchedProvider ? matchedProvider.value : null;

    }

    if (!provider) {

        const defaultProvider = providers.find(item => {

            return getImageModelsForProvider(catalog, item.value).some(model => defaultModelPriority.includes(model.value));

        });

        provider = defaultProvider ? defaultProvider.value : providers[0].value;

    }

    const models = getImageModelsForProvider(catalog, provider);

    let model = preferredModel && models.some(item => item.value === preferredModel)
        ? preferredModel
        : null;

    if (!model) {

        model = defaultModelPriority.find(defaultModel => models.some(item => item.value === defaultModel)) || (models[0]?.value || null);

    }

    const routeOptions = getImageRouteOptions(catalog, provider, model);

    const size = routeOptions.sizes.includes('9:16')
        ? '9:16'
        : (routeOptions.sizes.includes('16:9') ? '16:9' : (routeOptions.sizes[0] || '1:1'));

    return {

        provider,

        model,

        size,

        resolution: routeOptions.resolutions[0] || null

    };

}



function getImageModelFromCatalog(catalog, provider, model) {

    const targetProvider = catalog?.providers?.find(item => item && item.value === provider);

    return targetProvider?.models?.find(item => item && item.value === model) || null;

}



function buildImageSelectOptions(options, selectedValue, placeholder) {

    const placeholderOption = placeholder ? `<option value="">${escapeHtml(placeholder)}</option>` : '';

    return placeholderOption + options.map(option => {

        const selected = option.value === selectedValue ? ' selected' : '';

        return `<option value="${escapeHtml(option.value)}"${selected}>${escapeHtml(option.label || option.value)}</option>`;

    }).join('');

}



function buildImageSizeOptionsHtml(sizes, selectedSize) {

    const sizeLabels = {

        '9:16': '9:16（竖版）',

        '16:9': '16:9（横版）',

        '1:1': '1:1（方形）'

    };

    return sizes.map(size => {

        const selected = size === selectedSize ? ' selected' : '';

        const label = sizeLabels[size] || size;

        return `<option value="${escapeHtml(size)}"${selected}>${escapeHtml(label)}</option>`;

    }).join('');

}



// 全局变量：模型配缃?

let IMAGE_MODELS = {};

let IMAGE_MODEL_CATALOG = { providers: [] };



// 加载模型配置

async function loadImageModels() {

    try {

        const response = await apiRequest('/api/image-generation/models');

        const data = await response.json();

        IMAGE_MODEL_CATALOG = normalizeImageModelCatalogResponse(data);

        IMAGE_MODELS = {};

        IMAGE_MODEL_CATALOG.providers.forEach(provider => {

            provider.models.forEach(model => {

                IMAGE_MODELS[model.value] = {

                    name: model.label,

                    provider: provider.value,

                    sizes: model.sizes,

                    resolutions: model.resolutions,

                    supports_reference: model.supports_reference

                };

            });

        });



        // 濉厖模型选择鍣?

        const providerSelect = document.getElementById('imageProviderSelect');

        const select = document.getElementById('imageModelSelect');

        if (select) {

            const defaultSelection = getDefaultImageSelection(

                IMAGE_MODEL_CATALOG,

                providerSelect?.value || null,

                select.value || null

            );

            if (providerSelect) {

                providerSelect.innerHTML = buildImageSelectOptions(

                    getImageProviders(IMAGE_MODEL_CATALOG),

                    defaultSelection.provider,

                    '选择服务商'

                );

                providerSelect.value = defaultSelection.provider || '';

            }

            select.innerHTML = buildImageSelectOptions(

                getImageModelsForProvider(IMAGE_MODEL_CATALOG, defaultSelection.provider),

                defaultSelection.model,

                '选择模型'

            );

            select.value = defaultSelection.model || '';

            if (defaultSelection.model) {

                updateImageGenerationParams();

            }

        }

    } catch (error) {

        console.error('Failed to load image models:', error);

    }

}



async function ensureImageModelCatalogLoaded() {

    if (IMAGE_MODEL_CATALOG?.providers?.length > 0) {

        return IMAGE_MODEL_CATALOG;

    }

    await loadImageModels();

    return IMAGE_MODEL_CATALOG;

}



function updateImageProviderModels() {

    const providerSelect = document.getElementById('imageProviderSelect');

    const modelSelect = document.getElementById('imageModelSelect');

    if (!providerSelect || !modelSelect) {

        return;

    }

    const selection = getDefaultImageSelection(IMAGE_MODEL_CATALOG, providerSelect.value, modelSelect.value);

    providerSelect.value = selection.provider || '';

    modelSelect.innerHTML = buildImageSelectOptions(

        getImageModelsForProvider(IMAGE_MODEL_CATALOG, selection.provider),

        selection.model,

        '选择模型'

    );

    modelSelect.value = selection.model || '';

    updateImageGenerationParams();

}



// 更新参数面板

function updateImageGenerationParams() {

    const providerSelect = document.getElementById('imageProviderSelect');

    const select = document.getElementById('imageModelSelect');

    const paramsContainer = document.getElementById('imageGenerationParams');

    if (!select || !paramsContainer) {

        return;

    }

    const providerKey = providerSelect?.value || getDefaultImageSelection(IMAGE_MODEL_CATALOG, null, select.value).provider;

    const modelKey = select.value;



    if (!modelKey) {

        paramsContainer.style.display = 'none';

        return;

    }



    const modelConfig = getImageRouteOptions(IMAGE_MODEL_CATALOG, providerKey, modelKey);

    if (!modelConfig || modelConfig.sizes.length === 0) return;



    // 鉁?保存用户当前选择的比例（如果存在锛?

    const currentSizeSelect = document.getElementById('imageSizeSelect');

    const userSelectedSize = currentSizeSelect ? currentSizeSelect.value : null;



    paramsContainer.style.display = 'block';



    let html = '<div style="display: flex; flex-direction: column; gap: 10px;">';



    // 比例选择

    html += '<div>';

    html += '<label style="font-size: 11px; color: #888; display: block; margin-bottom: 5px;">图片比例</label>';

    html += '<select id="imageSizeSelect" class="form-input" style="font-size: 13px; padding: 6px 10px;">';



    // 鉁?使用用户之前选择的比例，如果新模型支持的话；否则使用榛樿鍊?

    let selectedSize;

    if (userSelectedSize && modelConfig.sizes.includes(userSelectedSize)) {

        selectedSize = userSelectedSize;

    } else {

        selectedSize = modelConfig.sizes.includes('9:16') ? '9:16' : modelConfig.sizes[0];

    }



    const sizeLabels = {

        '9:16': '9:16（竖版）',

        '16:9': '16:9（横版）'

    };

    modelConfig.sizes.forEach(size => {

        const selected = size === selectedSize ? 'selected' : '';

        const label = sizeLabels[size] || size;

        html += `<option value="${size}" ${selected}>${label}</option>`;

    });

    html += '</select>';

    html += '</div>';



    // 分辨鐜囬€夋嫨锛堝果支持）

    if (modelConfig.resolutions.length > 0) {

        html += '<div>';

        html += '<label style="font-size: 11px; color: #888; display: block; margin-bottom: 5px;">分辨率</label>';

        html += '<select id="imageResolutionSelect" class="form-input" style="font-size: 13px; padding: 6px 10px;">';

        modelConfig.resolutions.forEach(res => {

            html += `<option value="${res}">${res}</option>`;

        });

        html += '</select>';

        html += '</div>';

    }



    // 选择参考图按钮

    html += '<div>';

    html += '<label style="font-size: 11px; color: #888; display: block; margin-bottom: 5px;">参考图</label>';

    html += `<button class="secondary-button" onclick="openReferenceImageSelector()" style="width: 100%; font-size: 13px; padding: 6px 10px;">

        <span id="referenceImageButtonText">选择参考图（${APP_STATE.selectedReferenceImagesForGeneration.length}）</span>

    </button>`;

    html += '</div>';



    html += '</div>';

    paramsContainer.innerHTML = html;

}



function resolveThreeViewReferenceImageId(images) {

    if (!Array.isArray(images)) {

        return null;

    }



    const referenceImage = images.find(img => {

        return img

            && img.status === 'completed'

            && Boolean(img.is_reference)

            && Boolean(img.image_path);

    });



    if (!referenceImage) {

        return null;

    }



    const parsedId = parseInt(referenceImage.id, 10);

    return Number.isFinite(parsedId) ? parsedId : null;

}



function getCardImageGenerationSize(generationMode, selectedSize) {

    return generationMode === 'three_view' ? '16:9' : (selectedSize || '1:1');

}



// 生成图片

async function generateCardImage(generationMode = 'default') {

    const providerKey = document.getElementById('imageProviderSelect')?.value || null;

    const modelKey = document.getElementById('imageModelSelect').value;

    if (!providerKey || !modelKey) {

        alert('请选择作图服务商和模型');

        return;

    }



    const size = getCardImageGenerationSize(
        generationMode,
        document.getElementById('imageSizeSelect')?.value || '1:1'
    );

    const resolution = document.getElementById('imageResolutionSelect')?.value || null;

    const n = 1;  // 固定涓?寮?



    // 使用APP_STATE涓瓨储的鍙傝€冨浘IDs

    let referenceImageIds = APP_STATE.selectedReferenceImagesForGeneration || [];



    try {

        if (generationMode === 'three_view') {

            const generatedImagesResponse = await apiRequest(`/api/cards/${APP_STATE.selectedCardForPrompt}/generated-images`);

            if (!generatedImagesResponse.ok) {

                throw new Error('加载主体素材图失败');

            }



            const generatedImages = await generatedImagesResponse.json();

            const referenceImageId = resolveThreeViewReferenceImageId(generatedImages);

            if (!referenceImageId) {

                showAlertDialog('请先生成一张图片，并勾选为主体素材图后，再生成三视图。');

                return;

            }



            referenceImageIds = [referenceImageId];

        }

        // 鉁?在生成图片前，先鑷姩保存当前的prompt鍜岄格模鏉?

        await saveCardPrompt({ silent: true });



        showToast(generationMode === 'three_view' ? '正在提交三视图作图任务...' : '正在提交作图任务...', 'info');



        const requestBody = {

            provider: providerKey,

            model: modelKey,

            size: size,

            n: n,

            reference_image_ids: referenceImageIds,

            generation_mode: generationMode

        };



        if (resolution) {

            requestBody.resolution = resolution;

        }



        const response = await apiRequest(`/api/cards/${APP_STATE.selectedCardForPrompt}/generate-image`, {

            method: 'POST',

            body: JSON.stringify(requestBody)

        });



        if (response.ok) {

            const result = await response.json();

            showToast(generationMode === 'three_view' ? '三视图作图任务已提交，生成中...' : '作图任务已提交，生成中...', 'success');

            const currentCard = APP_STATE.cards.find(card => card.id === APP_STATE.selectedCardForPrompt);

            if (currentCard) {

                currentCard.is_generating_images = true;

                currentCard.generating_count = Math.max(1, (Number.parseInt(currentCard.generating_count, 10) || 0) + 1);

            }



            // 立即刷新生成图片列表

            await loadGeneratedImages(APP_STATE.selectedCardForPrompt);



            // 鍚姩图片生成鐘舵€佽疆璇?

            startImageStatusPolling();

        } else {

            const error = await response.json();

            showToast(`作图失败: ${error.detail}`, 'error');

        }

    } catch (error) {

        console.error('Failed to generate image:', error);

        showToast('作图失败', 'error');

    }

}



// 打开AI作图鍙傝€冨浘选择妯℃€佹

async function openReferenceImageSelector() {

    if (!APP_STATE.selectedCardForPrompt) {

        showToast('请先选择主体卡片', 'error');

        return;

    }



    try {

        // 获取该卡片的鎵€有图鐗?

        const response = await apiRequest(`/api/cards/${APP_STATE.selectedCardForPrompt}/generated-images`);

        const images = await response.json();



        // 鍙樉示已完成的图鐗?

        const completedImages = images.filter(img => img.status === 'completed');



        if (completedImages.length === 0) {

            showToast('该主体暂无可用图片', 'info');

            return;

        }



        // 濉厖图片列表

        const listContainer = document.getElementById('aiReferenceImageList');

        listContainer.innerHTML = '';



        const grid = document.createElement('div');

        grid.style.display = 'grid';

        grid.style.gridTemplateColumns = 'repeat(3, 1fr)';

        grid.style.gap = '10px';



        completedImages.forEach(img => {

            const isSelected = APP_STATE.selectedReferenceImagesForGeneration.includes(img.id);



            const imageItem = document.createElement('div');

            imageItem.style.cssText = 'position: relative; aspect-ratio: 1; border: 2px solid ' + (isSelected ? '#4caf50' : '#2a2a2a') + '; border-radius: 4px; overflow: hidden; cursor: pointer;';

            imageItem.dataset.imageId = img.id;



            imageItem.innerHTML = `

                <img src="${img.image_path}" style="width: 100%; height: 100%; object-fit: cover;">

                <div style="position: absolute; top: 5px; right: 5px; background: ${isSelected ? '#4caf50' : 'rgba(0,0,0,0.6)'}; color: white; border-radius: 50%; width: 24px; height: 24px; display: flex; align-items: center; justify-content: center; font-size: 16px;">

                    ${isSelected ? '✓' : ''}

                </div>

            `;



            imageItem.onclick = () => toggleAiReferenceImageSelection(img.id, imageItem);

            grid.appendChild(imageItem);

        });



        listContainer.appendChild(grid);



        // 显示妯℃€佹

        document.getElementById('aiReferenceImageModal').classList.add('active');

    } catch (error) {

        console.error('Failed to load images for reference selection:', error);

        showToast('加载图片失败', 'error');

    }

}



// 切换AI鍙傝€冨浘选择鐘舵€?

function toggleAiReferenceImageSelection(imageId, element) {

    const index = APP_STATE.selectedReferenceImagesForGeneration.indexOf(imageId);



    if (index > -1) {

        // 取消选择

        APP_STATE.selectedReferenceImagesForGeneration.splice(index, 1);

        element.style.borderColor = '#2a2a2a';

        element.querySelector('div').innerHTML = '';

        element.querySelector('div').style.background = 'rgba(0,0,0,0.6)';

    } else {

        // 选择

        APP_STATE.selectedReferenceImagesForGeneration.push(imageId);

        element.style.borderColor = '#4caf50';

        element.querySelector('div').innerHTML = '✓';

        element.querySelector('div').style.background = '#4caf50';

    }

}



// 鍏ㄩ€堿I鍙傝€冨浘

function selectAllAiReferenceImages() {

    const listContainer = document.getElementById('aiReferenceImageList');

    const imageItems = listContainer.querySelectorAll('[data-image-id]');



    APP_STATE.selectedReferenceImagesForGeneration = [];



    imageItems.forEach(item => {

        const imageId = parseInt(item.dataset.imageId);

        APP_STATE.selectedReferenceImagesForGeneration.push(imageId);

        item.style.borderColor = '#4caf50';

        item.querySelector('div').innerHTML = '✓';

        item.querySelector('div').style.background = '#4caf50';

    });

}



// 取消鍏ㄩ€堿I鍙傝€冨浘

function unselectAllAiReferenceImages() {

    const listContainer = document.getElementById('aiReferenceImageList');

    const imageItems = listContainer.querySelectorAll('[data-image-id]');



    APP_STATE.selectedReferenceImagesForGeneration = [];



    imageItems.forEach(item => {

        item.style.borderColor = '#2a2a2a';

        item.querySelector('div').innerHTML = '';

        item.querySelector('div').style.background = 'rgba(0,0,0,0.6)';

    });

}



// 纭选择AI鍙傝€冨浘

function confirmAiReferenceImages() {

    // 更新按钮文本

    const buttonText = document.getElementById('referenceImageButtonText');

    if (buttonText) {

        buttonText.textContent = `选择参考图（${APP_STATE.selectedReferenceImagesForGeneration.length}）`;

    }



    // 关闭模态框

    closeAiReferenceImageModal();



    showToast(`已选择 ${APP_STATE.selectedReferenceImagesForGeneration.length} 张参考图`, 'success');

}



// 关闭AI参考图选择模态框

function closeAiReferenceImageModal() {

    document.getElementById('aiReferenceImageModal').classList.remove('active');

}



// 加载生成的图鐗?

async function loadGeneratedImages(cardId, prefetchedImages = null) {
    try {

        const images = Array.isArray(prefetchedImages)

            ? prefetchedImages

            : await (async () => {

                const response = await apiRequest(`/api/cards/${cardId}/generated-images`);

                return response.json();

            })();

        const card = APP_STATE.cards.find(item => item.id === cardId);

        if (card) {

            card.generated_images = images;

        }

        const container = document.getElementById('generatedImagesContainer');

        if (!container) return false;



        if (images.length === 0) {

            container.innerHTML = '<div style="text-align: center; padding: 20px; color: #555; font-size: 12px;">暂无生成图片</div>';

            return false;

        }



        container.innerHTML = '<div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px;"></div>';

        const grid = container.firstChild;



        // 妫€查是否有处理涓殑图片

        const hasProcessing = images.some(img => img.status === 'processing');



        images.forEach(img => {

            const statusBadge = {

                'processing': '<span style="color: #ff9800;">生成中</span>',

                'completed': '',

                'failed': '<span style="color: #f44336;">失败</span>'

            }[img.status] || '';



            const imageHtml = `

                <div class="generated-image-item" style="position: relative; aspect-ratio: 1; background: #0a0a0a; border: 1px solid ${img.is_reference ? '#4caf50' : '#2a2a2a'}; border-radius: 4px; overflow: hidden; cursor: pointer;">

                    ${img.status === 'completed' ? `

                        <img src="${img.image_path}" alt="Generated" style="width: 100%; height: 100%; object-fit: cover;" onclick="openGeneratedImageModal(${img.id})">

                        <div class="reference-badge" style="position: absolute; top: 5px; right: 5px; z-index: 2;">

                            <input type="checkbox" class="reference-checkbox" data-image-id="${img.id}" ${img.is_reference ? 'checked' : ''}

                                onclick="event.stopPropagation(); toggleReferenceImage(${img.id}, this.checked)"

                                style="width: 18px; height: 18px; cursor: pointer;">

                        </div>

                        <div class="image-action-buttons" style="position: absolute; bottom: 5px; right: 5px; display: flex; gap: 5px; opacity: 0; transition: opacity 0.2s;">

                            <button onclick="event.stopPropagation(); downloadGeneratedImage('${img.image_path}')"

                                style="background: rgba(0,0,0,0.7); color: white; border: none; padding: 4px 8px; border-radius: 2px; font-size: 11px; cursor: pointer;">下载</button>

                            <button onclick="event.stopPropagation(); deleteGeneratedImage(${img.id})"

                                style="background: rgba(0,0,0,0.7); color: white; border: none; padding: 4px 8px; border-radius: 2px; font-size: 11px; cursor: pointer;">删除</button>

                        </div>

                    ` : `

                        <div style="display: flex; align-items: center; justify-content: center; height: 100%; color: #666; font-size: 12px;">

                            ${statusBadge} ${img.status === 'processing' ? '生成中...' : '生成失败'}

                        </div>

                        ${img.status === 'failed' ? `

                            <button class="delete-generated-image" onclick="event.stopPropagation(); deleteGeneratedImage(${img.id})"

                                style="position: absolute; bottom: 5px; right: 5px; background: rgba(0,0,0,0.7); color: white; border: none; padding: 4px 8px; border-radius: 2px; font-size: 11px; cursor: pointer; opacity: 0; transition: opacity 0.2s;">删除</button>

                        ` : ''}

                    `}

                </div>

            `;



            grid.innerHTML += imageHtml;

        });



        // 添加hover效果

        container.querySelectorAll('.generated-image-item').forEach(item => {

            item.addEventListener('mouseenter', () => {

                const actionButtons = item.querySelector('.image-action-buttons');

                const deleteBtn = item.querySelector('.delete-generated-image');

                if (actionButtons) actionButtons.style.opacity = '1';

                if (deleteBtn) deleteBtn.style.opacity = '1';

            });

            item.addEventListener('mouseleave', () => {

                const actionButtons = item.querySelector('.image-action-buttons');

                const deleteBtn = item.querySelector('.delete-generated-image');

                if (actionButtons) actionButtons.style.opacity = '0';

                if (deleteBtn) deleteBtn.style.opacity = '0';

            });

        });



        // 返回鏄惁鏈夊理中的图鐗?

        return hasProcessing;



    } catch (error) {

        console.error('Failed to load generated images:', error);

        container.innerHTML = '<div style="text-align: center; padding: 20px; color: #f44336; font-size: 12px;">加载失败</div>';

        return false;

    }

}



// 切换鍙傝€冨浘鐘舵€?

async function toggleReferenceImage(imageId, isChecked) {

    try {

        // 鉁?保存右侧生成图片列表的滚动位缃?

        const generatedImagesContainer = document.getElementById('generatedImagesContainer');

        const scrollTop = generatedImagesContainer ? generatedImagesContainer.scrollTop : 0;



        // 获取鎵€鏈夐€変腑的checkbox

        const allChecked = Array.from(document.querySelectorAll('.reference-checkbox:checked'))

            .map(cb => parseInt(cb.dataset.imageId));

        const selectedCard = (Array.isArray(APP_STATE.cards) ? APP_STATE.cards : []).find(
            card => card.id === APP_STATE.selectedCardForPrompt
        );
        const allowEmptySelection = Boolean(selectedCard && selectedCard.card_type === '场景');


        // 角色卡至少保留一张参考图；场景卡允许全部取消

        if (allChecked.length === 0 && !allowEmptySelection) {

            // 鎭㈠checkbox鐘舵€?

            const checkbox = document.querySelector(`.reference-checkbox[data-image-id="${imageId}"]`);

            if (checkbox) {

                checkbox.checked = true;

            }



            showToast('至少要保留一张主体素材图', 'warning');

            return;

        }



        const response = await apiRequest(`/api/cards/${APP_STATE.selectedCardForPrompt}/reference-images`, {

            method: 'PUT',

            body: JSON.stringify({ generated_image_ids: allChecked })

        });



        if (response.ok) {

            // 鉁?鍙埛新数鎹紝不刷新整涓〉闈?

            await refreshSubjectCardsData();



            // 鉁?重新渲染左侧卡片列表，更鏂伴览图

            renderSubjectCards();



            // 鉁?重新加载生成的图片（右侧锛?

            await loadGeneratedImages(APP_STATE.selectedCardForPrompt);



            // 鉁?鎭㈠滚动位置

            setTimeout(() => {

                const refreshedContainer = document.getElementById('generatedImagesContainer');

                if (refreshedContainer) {

                    refreshedContainer.scrollTop = scrollTop;

                }

            }, 50);

        }

    } catch (error) {

        console.error('Failed to toggle reference image:', error);

    }

}



// 下载生成的图鐗?

function downloadGeneratedImage(imageUrl) {

    const link = document.createElement('a');

    link.href = imageUrl;

    link.download = imageUrl.split('/').pop();

    link.click();

}



// 删除生成的图鐗?

async function deleteGeneratedImage(imageId) {

    const confirmed = await showConfirmModal('确定要删除这张图片吗？');

    if (!confirmed) return;



    try {

        const response = await apiRequest(`/api/generated-images/${imageId}`, {

            method: 'DELETE'

        });



        if (response.ok) {

            showToast('图片已删除', 'success');

            await reloadSubjectStepPreserveState();

        } else if (response.status === 400) {

            // 显示鍚庣返回的错璇俊鎭紙如：不能删除鏈€后一张主体素材图锛?

            const error = await response.json();

            showToast(error.detail || '删除失败', 'error');

        } else {

            showToast('删除失败', 'error');

        }

    } catch (error) {

        console.error('Failed to delete generated image:', error);

        showToast('删除失败', 'error');

    }

}



// 打开生成图片的模鎬佹

async function openGeneratedImageModal(imageId) {

    try {

        const response = await apiRequest(`/api/cards/${APP_STATE.selectedCardForPrompt}/generated-images`);

        const images = await response.json();

        const currentImage = images.find(img => img.id === imageId);



        if (!currentImage || currentImage.status !== 'completed') return;



        // 使用现有的图片模鎬佹

        APP_STATE.imageModal = {

            isOpen: true,

            images: [{ id: imageId, image_path: currentImage.image_path }],

            currentIndex: 0,

            cardId: APP_STATE.selectedCardForPrompt

        };



        updateImageModal();

        document.getElementById('imageModal').classList.add('active');

        // 生成图片不显示删除按閽紙在右侧栏已有删除按钮锛?

        document.getElementById('deleteImage').style.display = 'none';

    } catch (error) {

        console.error('Failed to open image modal:', error);

    }

}



// Toast提示函数

function showToast(message, type = 'info', durationMs = 2000) {

    // 创建toast元素

    const toast = document.createElement('div');

    toast.style.cssText = `

        position: fixed;

        top: 80px;

        right: 20px;

        padding: 15px 20px;

        background: ${type === 'success' ? '#4caf50' : type === 'error' ? '#f44336' : '#2196f3'};

        color: white;

        border-radius: 4px;

        box-shadow: 0 2px 10px rgba(0,0,0,0.2);

        z-index: 10000;

        font-size: 14px;

        max-width: 300px;

        animation: slideIn 0.3s ease-out;

    `;

    toast.textContent = message;



    // 添加滑入动画

    if (!document.getElementById('toastStyle')) {

        const style = document.createElement('style');

        style.id = 'toastStyle';

        style.textContent = `

            @keyframes slideIn {

                from {

                    transform: translateX(400px);

                    opacity: 0;

                }

                to {

                    transform: translateX(0);

                    opacity: 1;

                }

            }

            @keyframes slideOut {

                from {

                    transform: translateX(0);

                    opacity: 1;

                }

                to {

                    transform: translateX(400px);

                    opacity: 0;

                }

            }

        `;

        document.head.appendChild(style);

    }



    document.body.appendChild(toast);



    const duration = typeof durationMs === 'number' ? durationMs : 2000;

    // 鑷姩移除

    setTimeout(() => {

        toast.style.animation = 'slideOut 0.3s ease-out';

        setTimeout(() => toast.remove(), 300);

    }, duration);

}



// ==================== 分镜表编辑模鎬佹 ====================



// 打开分镜表编辑模鎬佹

function openStoryboardEditModal(shots) {

    storyboardData = shots || [];

    renderStoryboardEditModal();

    document.getElementById('storyboardEditModal').classList.add('active');

}



// 关闭分镜表编辑模鎬佹

function closeStoryboardEditModal() {

    document.getElementById('storyboardEditModal').classList.remove('active');

    storyboardData = [];

}



// 渲染分镜表编辑模鎬佹

function renderStoryboardEditModal() {

    const tbody = document.getElementById('storyboardEditTableBody');

    if (!tbody) return;



    tbody.innerHTML = storyboardData.map((shot, index) => {

        // 适配characters数组格式

        let characters = [];

        if (Array.isArray(shot.characters)) {

            characters = shot.characters;

        } else if (shot.characters && typeof shot.characters === 'object') {

            // 向后鍏煎旧格寮?

            characters = [shot.characters];

        }



        // 生成角色列表的HTML

        const charactersHtml = characters.map((char, charIndex) => `

            <div class="character-row" style="display: flex; gap: 5px; margin-bottom: 5px; align-items: center;">

                <input type="text"

                       value="${escapeHtml(char.name || '')}"

                       data-char-index="${charIndex}"

                       data-field="char_name"

                       placeholder="角色名称"

                       style="flex: 1; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 4px 6px; border-radius: 2px; font-size: 12px;">

                <select data-char-index="${charIndex}"

                        data-field="char_type"

                        style="width: 80px; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 4px 6px; border-radius: 2px; font-size: 12px;">

                    <option value="角色" ${char.type === '角色' ? 'selected' : ''}>角色</option>

                    <option value="场景" ${char.type === '场景' ? 'selected' : ''}>场景</option>

                    <option value="道具" ${char.type === '道具' ? 'selected' : ''}>道具</option>

                </select>

                <button onclick="removeCharacterFromShot(${index}, ${charIndex})"

                        style="background: #d32f2f; color: white; border: none; padding: 4px 8px; border-radius: 2px; cursor: pointer; font-size: 11px;">

                    删除

                </button>

            </div>

        `).join('');



        return `

            <tr data-index="${index}">

                <td style="padding: 8px; border: 1px solid #2a2a2a;">

                    <input type="text" value="${escapeHtml(shot.shot_number || '')}"

                           data-field="shot_number"

                           style="width: 100%; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 6px; border-radius: 2px;">

                </td>

                <td style="padding: 8px; border: 1px solid #2a2a2a;">

                    <input type="text" value="${escapeHtml(shot.shot_scale || '')}"

                           data-field="shot_scale"

                           style="width: 100%; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 6px; border-radius: 2px;">

                </td>

                <td style="padding: 8px; border: 1px solid #2a2a2a;">

                    <input type="text" value="${escapeHtml(shot.camera_angle || '')}"

                           data-field="camera_angle"

                           style="width: 100%; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 6px; border-radius: 2px;">

                </td>

                <td style="padding: 8px; border: 1px solid #2a2a2a;">

                    <input type="text" value="${escapeHtml(shot.camera_movement || '')}"

                           data-field="camera_movement"

                           style="width: 100%; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 6px; border-radius: 2px;">

                </td>

                <td style="padding: 8px; border: 1px solid #2a2a2a; min-width: 200px;">

                    <div class="characters-container" data-shot-index="${index}">

                        ${charactersHtml || '<div style="color: #666; font-size: 12px;">暂无角色</div>'}

                        <button onclick="addCharacterToShot(${index})"

                                style="background: #4caf50; color: white; border: none; padding: 4px 8px; border-radius: 2px; cursor: pointer; font-size: 11px; margin-top: 5px; width: 100%;">

                            + 添加角色

                        </button>

                    </div>

                </td>

                <td style="padding: 8px; border: 1px solid #2a2a2a;">

                    <textarea data-field="script_excerpt" rows="3"

                              style="width: 100%; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 6px; border-radius: 2px; resize: vertical;">${escapeHtml(shot.script_excerpt || '')}</textarea>

                </td>

                <td style="padding: 8px; border: 1px solid #2a2a2a;">

                    <textarea data-field="visual" rows="3"

                              style="width: 100%; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 6px; border-radius: 2px; resize: vertical;">${escapeHtml(shot.visual || '')}</textarea>

                </td>

                <td style="padding: 8px; border: 1px solid #2a2a2a;">

                    <textarea data-field="dialogue" rows="3"

                              style="width: 100%; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 6px; border-radius: 2px; resize: vertical;">${escapeHtml(shot.dialogue || '')}</textarea>

                </td>

                <td style="padding: 8px; border: 1px solid #2a2a2a;">

                    <input type="text" value="${escapeHtml(shot.sound_effects || '')}"

                           data-field="sound_effects"

                           style="width: 100%; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 6px; border-radius: 2px;">

                </td>

                <td style="padding: 8px; border: 1px solid #2a2a2a;">

                    <input type="text" value="${escapeHtml(shot.transition || '')}"

                           data-field="transition"

                           style="width: 100%; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 6px; border-radius: 2px;">

                </td>

                <td style="padding: 8px; border: 1px solid #2a2a2a;">

                    <input type="text" value="${escapeHtml(shot.duration || '15s')}"

                           data-field="duration"

                           style="width: 100%; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 6px; border-radius: 2px;">

                </td>

                <td style="padding: 8px; border: 1px solid #2a2a2a; text-align: center;">

                    <button onclick="deleteStoryboardRow(${index})"

                            style="background: #d32f2f; color: white; border: none; padding: 6px 12px; border-radius: 2px; cursor: pointer; font-size: 12px;">

                        删除镜头

                    </button>

                </td>

            </tr>

        `;

    }).join('');

}



// 添加角色到镜澶?

function addCharacterToShot(shotIndex) {

    if (!storyboardData[shotIndex]) return;



    if (!Array.isArray(storyboardData[shotIndex].characters)) {

        storyboardData[shotIndex].characters = [];

    }



    // 添加鏂拌色（ai_prompt为空，后缁垱建主体时不会设置prompt锛?

    storyboardData[shotIndex].characters.push({

        name: '',

        type: '角色',

        ai_prompt: ''

    });



    renderStoryboardEditModal();

}



// 从镜头删闄よ鑹?

function removeCharacterFromShot(shotIndex, charIndex) {

    if (!storyboardData[shotIndex]) return;

    if (!Array.isArray(storyboardData[shotIndex].characters)) return;



    if (confirm('确定要删除这个角色吗？')) {

        storyboardData[shotIndex].characters.splice(charIndex, 1);

        renderStoryboardEditModal();

    }

}



// 添加鏂拌

function addStoryboardRow() {

    const maxNumber = Math.max(...storyboardData.map(s => parseInt(s.shot_number) || 0), 0);

    storyboardData.push({

        shot_number: String(maxNumber + 1),

        shot_scale: '',

        camera_angle: '',

        camera_movement: '',

        characters: [],  // 使用数组格式

        script_excerpt: '',

        visual: '',

        dialogue: '',

        sound_effects: '',

        transition: '',

        duration: '15s'

    });

    renderStoryboardEditModal();

}



// 删除琛?

function deleteStoryboardRow(index) {

    if (confirm('确定要删除这个镜头吗？')) {

        storyboardData.splice(index, 1);

        renderStoryboardEditModal();

    }

}



// 收集表格数据

function collectStoryboardData() {

    const rows = document.querySelectorAll('#storyboardEditTableBody tr');

    const collected = [];



    rows.forEach((row) => {

        const shotNumber = row.querySelector('[data-field="shot_number"]')?.value.trim();

        if (!shotNumber) return; // 跳过空镜鍙?



        // 获取鍘熷索引，用于匹配原有的ai_prompt

        const index = parseInt(row.getAttribute('data-index'));

        const originalShot = storyboardData[index] || {};

        const originalCharacters = Array.isArray(originalShot.characters) ? originalShot.characters : [];



        // 收集该镜头的鎵€鏈夎鑹?

        const characterRows = row.querySelectorAll('.character-row');

        const characters = [];



        characterRows.forEach((charRow, charIndex) => {

            const nameInput = charRow.querySelector('[data-field="char_name"]');

            const typeSelect = charRow.querySelector('[data-field="char_type"]');



            if (nameInput && typeSelect) {

                const name = nameInput.value.trim();

                const type = typeSelect.value;



                // 保留原有的ai_prompt锛堥€氳繃索引匹配锛?

                const originalChar = originalCharacters[charIndex] || {};

                const ai_prompt = originalChar.ai_prompt || '';



                characters.push({

                    name: name,

                    type: type,

                    ai_prompt: ai_prompt  // 保留原有的ai_prompt

                });

            }

        });



        const shot = {

            shot_number: shotNumber,

            shot_scale: row.querySelector('[data-field="shot_scale"]')?.value.trim() || '',

            camera_angle: row.querySelector('[data-field="camera_angle"]')?.value.trim() || '',

            camera_movement: row.querySelector('[data-field="camera_movement"]')?.value.trim() || '',

            characters: characters,  // 使用收集到的角色数组

            script_excerpt: row.querySelector('[data-field="script_excerpt"]')?.value.trim() || '',

            visual: row.querySelector('[data-field="visual"]')?.value.trim() || '',

            dialogue: row.querySelector('[data-field="dialogue"]')?.value.trim() || '',

            sound_effects: row.querySelector('[data-field="sound_effects"]')?.value.trim() || '',

            transition: row.querySelector('[data-field="transition"]')?.value.trim() || '',

            duration: row.querySelector('[data-field="duration"]')?.value.trim() || '15s'

        };



        collected.push(shot);

    });



    return collected;

}



// 提交分镜琛?

async function submitStoryboard() {

    const shots = collectStoryboardData();



    if (shots.length === 0) {

        showToast('请至少添加一个镜头', 'warning');

        return;

    }



    try {

        showToast('正在创建主体和镜头...', 'info');



        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/create-from-storyboard`, {

            method: 'POST',

            body: JSON.stringify({ shots })

        });



        if (response.ok) {

            const result = await response.json();

            showToast(`创建成功！新增 ${result.created_subjects} 个主体，${result.created_shots} 个镜头`, 'success');

            closeStoryboardEditModal();



            // 刷新故事板界闈紝显示鏂板的镜澶?

            await switchStep(3);

        } else {

            const error = await response.json();

            showToast(`创建失败: ${error.detail || '未知错误'}`, 'error');

        }

    } catch (error) {

        console.error('Failed to submit storyboard:', error);

        showToast('创建失败', 'error');

    }

}



// ==================== 分镜图生成相关函鏁?====================



// 打开分镜图生成弹绐?

async function openStoryboardImageGenerator(shotId) {

    const shot = APP_STATE.shots.find(s => s.id === shotId);

    if (!shot) {

        showToast('镜头不存在', 'error');

        return;

    }



    // 妫€查是否有sora_prompt

    if (!shot.sora_prompt || shot.sora_prompt.trim() === '') {

        showToast('请先生成SORA提示词', 'error');

        return;

    }



    try {

        // 加载模板

        const [requirementsRes, stylesRes] = await Promise.all([

            apiRequest('/api/storyboard-templates/requirements'),

            apiRequest('/api/storyboard-templates/styles'),

            ensureImageModelCatalogLoaded()

        ]);



        const requirements = await requirementsRes.json();

        const styles = await stylesRes.json();



        // 获取榛樿模板

        const defaultRequirement = requirements.find(t => t.is_default) || requirements[0];

        const defaultStyle = styles.find(t => t.is_default) || styles[0];

        const defaultImageSelection = getDefaultImageSelection(

            IMAGE_MODEL_CATALOG,

            getEpisodeDetailImagesProvider() || null,

            shot.storyboard_image_model || getEpisodeDetailImagesModel()

        );

        const storyboardRouteOptions = getImageRouteOptions(

            IMAGE_MODEL_CATALOG,

            defaultImageSelection.provider,

            defaultImageSelection.model

        );



        // 创建弹窗

        const modal = document.createElement('div');

        modal.className = 'modal active';

        modal.id = 'storyboardImageGeneratorModal';

        modal.innerHTML = `

            <div class="modal-backdrop" onclick="closeStoryboardImageGeneratorModal()"></div>

            <div class="modal-content" style="max-width: 900px; max-height: 90vh; overflow-y: auto; padding: 20px 24px;">

                <div style="text-align: center; padding-bottom: 16px; border-bottom: 1px solid #2a2a2a; margin-bottom: 16px;">

                    <h2 style="margin: 0; font-size: 20px;">生成分镜图</h2>

                    <p style="margin: 4px 0 0 0; font-size: 13px; color: #888;">镜头 #${getShotLabel(shot)}</p>

                    <button onclick="closeStoryboardImageGeneratorModal()" style="position: absolute; top: 20px; right: 24px; background: transparent; border: none; color: #888; font-size: 24px; cursor: pointer; padding: 0; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; transition: color 0.2s;" onmouseover="this.style.color='#fff'" onmouseout="this.style.color='#888'">&times;</button>

                </div>



                <div style="display: grid; gap: 12px;">

                    <div style="background: #1a1a1a; padding: 16px; border-radius: 6px; border: 1px solid #2a2a2a;">

                        <div class="form-group" style="margin-bottom: 12px;">

                            <label style="margin-bottom: 8px; color: #fff; font-size: 14px; display: block;">

                                绘图要求模板

                            </label>

                            <select id="requirementTemplateSelect" class="form-input" onchange="updateRequirementContent()">

                                ${requirements.map(t => `<option value="${t.id}" ${t.id === defaultRequirement?.id ? 'selected' : ''}>${escapeHtml(t.name)}</option>`).join('')}

                            </select>

                        </div>



                        <div class="form-group">

                            <label style="font-size: 13px; color: #aaa; margin-bottom: 6px; display: block;">绘图要求内容</label>

                            <textarea id="requirementTextarea" class="form-textarea" rows="3" style="resize: vertical; font-size: 13px;" placeholder="输入绘图要求...">${escapeHtml(defaultRequirement?.content || '')}</textarea>

                        </div>

                    </div>



                    <div style="background: #1a1a1a; padding: 16px; border-radius: 6px; border: 1px solid #2a2a2a;">

                        <div class="form-group" style="margin-bottom: 12px;">

                            <label style="margin-bottom: 8px; color: #fff; font-size: 14px; display: block;">

                                绘画风格模板

                            </label>

                            <select id="styleTemplateSelect" class="form-input" onchange="updateStyleContent()">

                                ${styles.map(t => `<option value="${t.id}" ${t.id === defaultStyle?.id ? 'selected' : ''}>${escapeHtml(t.name)}</option>`).join('')}

                            </select>

                        </div>



                        <div class="form-group">

                            <label style="font-size: 13px; color: #aaa; margin-bottom: 6px; display: block;">绘画风格内容</label>

                            <textarea id="styleTextarea" class="form-textarea" rows="3" style="resize: vertical; font-size: 13px;" placeholder="输入绘画风格...">${escapeHtml(defaultStyle?.content || '')}</textarea>

                        </div>

                    </div>



                    <div style="background: #1a1a1a; padding: 16px; border-radius: 6px; border: 1px solid #2a2a2a;">

                        <label style="margin-bottom: 12px; color: #fff; font-size: 14px; display: block;">

                            生成参数

                        </label>

                        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;">

                            <div class="form-group">

                                <label style="font-size: 12px; color: #999; margin-bottom: 6px; display: block;">服务商</label>

                                <select id="storyboardImageProvider" class="form-input" style="font-size: 13px;" onchange="updateStoryboardImageProviderModels()">

                                    ${buildImageSelectOptions(getImageProviders(IMAGE_MODEL_CATALOG), defaultImageSelection.provider, '')}

                                </select>

                            </div>

                            <div class="form-group">

                                <label style="font-size: 12px; color: #999; margin-bottom: 6px; display: block;">模型</label>

                                <select id="storyboardImageModel" class="form-input" style="font-size: 13px;" onchange="handleStoryboardModelChange()">

                                    ${buildImageSelectOptions(

                                        getImageModelsForProvider(IMAGE_MODEL_CATALOG, defaultImageSelection.provider),

                                        defaultImageSelection.model,

                                        ''

                                    )}

                                </select>

                            </div>



                            <div class="form-group">

                                <label style="font-size: 12px; color: #999; margin-bottom: 6px; display: block;">尺寸</label>

                                <select id="storyboardImageSize" class="form-input" style="font-size: 13px;">

                                    ${buildImageSizeOptionsHtml(storyboardRouteOptions.sizes, defaultImageSelection.size)}

                                </select>

                            </div>



                            <div class="form-group" id="storyboardImageResolutionGroup">

                                <label style="font-size: 12px; color: #999; margin-bottom: 6px; display: block;">分辨率</label>

                                <select id="storyboardImageResolution" class="form-input" style="font-size: 13px;">

                                    ${storyboardRouteOptions.resolutions.map(resolution => {

                                        const selected = resolution === defaultImageSelection.resolution ? ' selected' : '';

                                        return `<option value="${escapeHtml(resolution)}"${selected}>${escapeHtml(resolution)}</option>`;

                                    }).join('')}

                                </select>

                            </div>

                        </div>

                    </div>

                </div>



                <div class="modal-actions" style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #2a2a2a; gap: 8px;">

                    <button class="secondary-button" onclick="closeStoryboardImageGeneratorModal()">取消</button>

                    <button class="primary-button" onclick="generateStoryboardImage(${shotId})" style="padding: 10px 24px;">

                        确认生成

                    </button>

                </div>

            </div>

        `;



        document.body.appendChild(modal);

        handleStoryboardModelChange();



        // 保存模板数据到window，供下拉框change事件使用

        window.storyboardTemplates = { requirements, styles };



    } catch (error) {

        console.error('Failed to load templates:', error);

        showToast('加载模板失败', 'error');

    }

}



// 更新绘图要求鍐呭

function updateRequirementContent() {

    const select = document.getElementById('requirementTemplateSelect');

    const textarea = document.getElementById('requirementTextarea');

    const templateId = parseInt(select.value);

    const template = window.storyboardTemplates.requirements.find(t => t.id === templateId);

    if (template) {

        textarea.value = template.content;

    }

}



// 更新绘画风格鍐呭

function updateStyleContent() {

    const select = document.getElementById('styleTemplateSelect');

    const textarea = document.getElementById('styleTextarea');

    const templateId = parseInt(select.value);

    const template = window.storyboardTemplates.styles.find(t => t.id === templateId);

    if (template) {

        textarea.value = template.content;

    }

}



// 关闭分镜图生成弹绐?

function closeStoryboardImageGeneratorModal() {

    const modal = document.getElementById('storyboardImageGeneratorModal');

    if (modal) {

        modal.remove();

    }

}



// 处理模型选择变化

function updateStoryboardImageProviderModels() {

    const providerSelect = document.getElementById('storyboardImageProvider');

    const modelSelect = document.getElementById('storyboardImageModel');

    if (!providerSelect || !modelSelect) {

        return;

    }

    const selection = getDefaultImageSelection(IMAGE_MODEL_CATALOG, providerSelect.value, modelSelect.value);

    providerSelect.value = selection.provider || '';

    modelSelect.innerHTML = buildImageSelectOptions(

        getImageModelsForProvider(IMAGE_MODEL_CATALOG, selection.provider),

        selection.model,

        ''

    );

    modelSelect.value = selection.model || '';

    handleStoryboardModelChange();

}



function handleStoryboardModelChange() {

    const providerSelect = document.getElementById('storyboardImageProvider');

    const modelSelect = document.getElementById('storyboardImageModel');

    const sizeSelect = document.getElementById('storyboardImageSize');

    const resolutionGroup = document.getElementById('storyboardImageResolutionGroup');

    const resolutionSelect = document.getElementById('storyboardImageResolution');



    if (!modelSelect || !sizeSelect || !resolutionGroup || !resolutionSelect) return;



    const selectedProvider = providerSelect?.value || getDefaultImageSelection(IMAGE_MODEL_CATALOG, null, modelSelect.value).provider;

    const selectedModel = modelSelect.value;

    const routeOptions = getImageRouteOptions(IMAGE_MODEL_CATALOG, selectedProvider, selectedModel);

    const currentSize = sizeSelect.value;

    const selectedSize = routeOptions.sizes.includes(currentSize)
        ? currentSize
        : (routeOptions.sizes.includes('9:16') ? '9:16' : (routeOptions.sizes[0] || '1:1'));

    sizeSelect.innerHTML = buildImageSizeOptionsHtml(routeOptions.sizes.length > 0 ? routeOptions.sizes : ['1:1'], selectedSize);

    if (routeOptions.resolutions.length === 0) {

        resolutionGroup.style.display = 'none';

        resolutionSelect.innerHTML = '';

    } else {

        resolutionGroup.style.display = 'block';

        const currentResolution = resolutionSelect.value;

        const selectedResolution = routeOptions.resolutions.includes(currentResolution)
            ? currentResolution
            : routeOptions.resolutions[0];

        resolutionSelect.innerHTML = routeOptions.resolutions.map(resolution => {

            const selected = resolution === selectedResolution ? ' selected' : '';

            return `<option value="${escapeHtml(resolution)}"${selected}>${escapeHtml(resolution)}</option>`;

        }).join('');

    }

}



// 生成分镜鍥?

async function generateStoryboardImage(shotId) {

    const requirement = document.getElementById('requirementTextarea').value.trim();

    const style = document.getElementById('styleTextarea').value.trim();

    const provider = document.getElementById('storyboardImageProvider')?.value || null;

    const model = document.getElementById('storyboardImageModel').value;

    const size = document.getElementById('storyboardImageSize').value;



    if (!requirement || !style) {

        showToast('请填写绘图要求和绘画风格', 'warning');

        return;

    }

    if (!provider || !model) {

        showToast('请选择作图服务商和模型', 'warning');

        return;

    }



    // 鉁?先构寤鸿求体锛堣取所有表鍗曞€硷級

    const requestBody = {

        requirement,

        style,

        provider,

        model,

        size

    };



    // 仅在当前模型支持分辨率时传递分辨率参数

    if (document.getElementById('storyboardImageResolutionGroup')?.style.display !== 'none') {

        const resolution = document.getElementById('storyboardImageResolution')?.value;

        if (resolution) {

            requestBody.resolution = resolution;

        }

    }



    // 鉁?读取完所鏈夊€煎悗，立即关闂脊绐?

    closeStoryboardImageGeneratorModal();



    // 鉁?显示正在提交的提绀?

    showToast('正在提交分镜图生成任务...', 'info');



    try {

        // 鉁?寮傛提交任务（不闃诲用户操作锛?

        const response = await apiRequest(`/api/shots/${shotId}/generate-storyboard-image`, {

            method: 'POST',

            body: JSON.stringify(requestBody)

        });



        if (response.ok) {

            const result = await response.json();



            // 鉁?显示提交成功提示

            showToast('分镜图生成任务已提交', 'success');



            // 刷新镜头列表

            const shotsUrl = `/api/episodes/${APP_STATE.currentEpisode}/shots`;

            const shotsResponse = await apiRequest(shotsUrl);

            APP_STATE.shots = await shotsResponse.json();



            // 如果创建了新的变体，选中瀹?

            if (result.shot_id !== shotId) {

                APP_STATE.currentShot = APP_STATE.shots.find(s => s.id === result.shot_id);

            }



            renderStoryboardShotsGrid(true);

            renderStoryboardSidebar();



            // 鉁?鍚姩杞以监控分镜图生成鐘舵€?

            console.log('[生成分镜图] 鍚姩杞监控鐘舵€?');

            startVideoStatusPolling();

        } else {

            const error = await response.json();

            showToast(`提交失败: ${error.detail}`, 'error');

        }

    } catch (error) {

        console.error('Failed to generate storyboard image:', error);

        showToast('提交失败', 'error');

    }

}



// 打开分镜鍥鹃览弹绐?

function openStoryboardImageModal(shotId) {

    const shot = APP_STATE.shots.find(s => s.id === shotId);

    if (!shot || !shot.storyboard_image_path) {

        return;

    }



    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'storyboardImagePreviewModal';

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="closeStoryboardImagePreviewModal()"></div>

        <div class="modal-content" style="width: 95vw; height: 95vh; padding: 0; background: #000; border: 1px solid #333; overflow: hidden; display: flex; flex-direction: column;">

            <div style="padding: 16px 24px; background: linear-gradient(180deg, #1a1a1a 0%, #0f0f0f 100%); border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; flex-shrink: 0;">

                <div>

                    <h2 style="margin: 0; font-size: 18px; color: #fff;">分镜图预览</h2>

                    <p style="margin: 4px 0 0 0; font-size: 13px; color: #888;">镜头 #${getShotLabel(shot)}</p>

                </div>

                <button onclick="closeStoryboardImagePreviewModal()" style="background: transparent; border: none; color: #888; font-size: 24px; cursor: pointer; padding: 0; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; transition: color 0.2s;" onmouseover="this.style.color='#fff'" onmouseout="this.style.color='#888'">&times;</button>

            </div>

            <div style="flex: 1; display: flex; align-items: center; justify-content: center; overflow: hidden; padding: 20px; background: #000; min-height: 0;">

                <img src="${escapeHtml(shot.storyboard_image_path)}" style="max-width: 100%; max-height: 100%; object-fit: contain; border-radius: 4px; box-shadow: 0 8px 32px rgba(0,0,0,0.8);" />

            </div>

            <div style="padding: 12px 24px; background: #0f0f0f; border-top: 1px solid #333; display: flex; justify-content: flex-end; gap: 8px; flex-shrink: 0;">

                <button class="secondary-button" onclick="closeStoryboardImagePreviewModal()">关闭</button>

                <button class="primary-button" onclick="downloadStoryboardImage(${shotId})">

                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: middle; margin-right: 4px;">

                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>

                        <polyline points="7 10 12 15 17 10"></polyline>

                        <line x1="12" y1="15" x2="12" y2="3"></line>

                    </svg>

                    下载图片

                </button>

            </div>

        </div>

    `;



    document.body.appendChild(modal);

}



// 关闭分镜鍥鹃览弹绐?

function closeStoryboardImagePreviewModal() {

    const modal = document.getElementById('storyboardImagePreviewModal');

    if (modal) {

        modal.remove();

    }

}



// 下载分镜鍥?

function downloadStoryboardImage(shotId) {

    const shot = APP_STATE.shots.find(s => s.id === shotId);

    if (!shot || !shot.storyboard_image_path) {

        return;

    }



    const a = document.createElement('a');

    a.href = shot.storyboard_image_path;

    a.download = `storyboard_${getShotLabel(shot)}.png`;

    a.target = '_blank';

    document.body.appendChild(a);

    a.click();

    document.body.removeChild(a);

}



function getShotTimelineOptionsForDetailImageGeneration(shot) {

    if (!shot) {

        return [{ index: 1, label: '镜头1', visualText: '' }];

    }



    let timeline = [];

    try {

        const rawTimeline = typeof shot.timeline_json === 'string'

            ? JSON.parse(shot.timeline_json)

            : shot.timeline_json;

        if (Array.isArray(rawTimeline)) {

            timeline = rawTimeline;

        } else if (rawTimeline && Array.isArray(rawTimeline.timeline)) {

            timeline = rawTimeline.timeline;

        }

    } catch (error) {

        timeline = [];

    }



    if (!Array.isArray(timeline) || timeline.length === 0) {

        return [{ index: 1, label: '镜头1', visualText: '' }];

    }



    return timeline.map((item, idx) => {

        const order = idx + 1;

        const visualText = String(item?.visual || item?.visual_text || '').trim();

        return {

            index: order,

            label: `镜头${order}`,

            visualText

        };

    });

}



function getShotDetailImagePromptOverrideMap(shot) {

    const rawValue = shot?.detail_image_prompt_overrides;

    if (!rawValue) {

        return {};

    }

    try {

        const parsed = typeof rawValue === 'string' ? JSON.parse(rawValue) : rawValue;

        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {

            return parsed;

        }

    } catch (error) {

        return {};

    }

    return {};

}



function buildInitialDetailImagesPromptMap(timelineOptions, overrideMap) {

    const promptMap = {};

    (timelineOptions || []).forEach(option => {

        const key = String(option.index);

        const overrideText = String(overrideMap?.[key] || '').trim();

        const fallbackText = String(option?.visualText || '').trim();

        promptMap[key] = overrideText || fallbackText || '';

    });

    return promptMap;

}



function handleDetailImagesSubShotChange() {

    const modalState = APP_STATE.detailImagesGenerateModalState;

    if (!modalState) {

        return;

    }

    const selectEl = document.getElementById('detailImagesSubShotSelect');

    const textareaEl = document.getElementById('detailImagesPromptText');

    if (!selectEl || !textareaEl) {

        return;

    }



    const previousIndex = Number.parseInt(modalState.currentSubShotIndex, 10) || 1;

    modalState.promptBySubShot[String(previousIndex)] = textareaEl.value || '';



    const currentIndex = Number.parseInt(selectEl.value, 10) || 1;

    modalState.currentSubShotIndex = currentIndex;

    textareaEl.value = modalState.promptBySubShot[String(currentIndex)] || '';

}



function openDetailImagesGenerateModal(shotId) {

    closeDetailImagesGenerateModal();

    const shot = APP_STATE.shots.find(s => s.id === shotId);

    const timelineOptions = getShotTimelineOptionsForDetailImageGeneration(shot);

    const detailPromptOverrideMap = getShotDetailImagePromptOverrideMap(shot);

    const promptBySubShot = buildInitialDetailImagesPromptMap(timelineOptions, detailPromptOverrideMap);

    const defaultSubShotIndex = timelineOptions[0]?.index || 1;

    APP_STATE.detailImagesGenerateModalState = {

        shotId,

        timelineOptions,

        promptBySubShot,

        currentSubShotIndex: defaultSubShotIndex

    };



    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'detailImagesGenerateModal';

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="closeDetailImagesGenerateModal()"></div>

        <div class="modal-content shot-image-size-modal">

            <div class="modal-header">

                <h3>生成镜头图</h3>

                <button class="modal-close" onclick="closeDetailImagesGenerateModal()">&times;</button>

            </div>

            <div class="modal-body shot-image-size-modal-body">

                <div class="form-group shot-image-size-group">

                    <label class="form-label">选择镜头</label>

                    <select id="detailImagesSubShotSelect" class="form-input shot-image-size-select" onchange="handleDetailImagesSubShotChange()">

                        ${timelineOptions.map(option => `

                            <option value="${option.index}">${escapeHtml(option.label)}</option>

                        `).join('')}

                    </select>

                </div>

                <div class="form-group shot-image-size-group">

                    <label class="form-label">镜头具体内容（可编辑）</label>

                    <textarea id="detailImagesPromptText"

                              class="form-input"

                              rows="6"

                              style="resize: vertical; line-height: 1.5; white-space: pre-wrap;"

                              placeholder="请输入该镜头的具体内容">${escapeHtml(promptBySubShot[String(defaultSubShotIndex)] || '')}</textarea>

                </div>

                <div class="shot-image-size-actions">

                    <button class="secondary-button shot-image-size-btn" onclick="closeDetailImagesGenerateModal()">取消</button>

                    <button class="primary-button shot-image-size-btn" onclick="confirmGenerateDetailImages(${shotId})">开始生成</button>

                </div>

            </div>

        </div>

    `;



    document.body.appendChild(modal);

}



function closeDetailImagesGenerateModal() {

    APP_STATE.detailImagesGenerateModalState = null;

    const modal = document.getElementById('detailImagesGenerateModal');

    if (modal) {

        modal.remove();

    }

}



function confirmGenerateDetailImages(shotId) {

    const modalState = APP_STATE.detailImagesGenerateModalState;

    const selectedSubShotRaw = document.getElementById('detailImagesSubShotSelect')?.value || '1';

    const promptTextarea = document.getElementById('detailImagesPromptText');

    const selectedSubShotIndex = Number.parseInt(selectedSubShotRaw, 10) || 1;

    let selectedPromptText = String(promptTextarea?.value || '').trim();

    if (modalState) {

        modalState.promptBySubShot[String(selectedSubShotIndex)] = selectedPromptText;

        selectedPromptText = modalState.promptBySubShot[String(selectedSubShotIndex)] || selectedPromptText;

    }

    closeDetailImagesGenerateModal();

    generateDetailImages(shotId, null, selectedSubShotIndex, selectedPromptText);

}



// 生成镜头细化图片

async function generateDetailImages(shotId, selectedSize = null, selectedSubShotIndex = 1, selectedSubShotText = '') {

    console.log('[生成镜头细化图片] ========== 函数寮€始执琛?shotId=', shotId);

    const imageSize = normalizeShotImageSize(selectedSize || getEpisodeShotImageSize());

    await ensureImageModelCatalogLoaded();

    const imageModel = getEpisodeDetailImagesModel();

    const imageSelection = getDefaultImageSelection(

        IMAGE_MODEL_CATALOG,

        getEpisodeDetailImagesProvider() || null,

        imageModel

    );

    const targetSubShotIndex = Number.parseInt(selectedSubShotIndex, 10) || 1;

    try {

        const response = await apiRequest(`/api/shots/${shotId}/generate-detail-images`, {

            method: 'POST',

            body: JSON.stringify({

                size: imageSize,

                provider: imageSelection.provider,

                model: imageSelection.model || imageModel,

                resolution: imageSelection.resolution,

                selected_sub_shot_index: targetSubShotIndex,

                selected_sub_shot_text: selectedSubShotText

            })

        });



        if (!response.ok) {

            const error = await response.json();

            throw new Error(error.detail || '生成失败');

        }



        const result = await response.json();

        const startedSubShotIndex = Number.parseInt(result?.selected_sub_shot_index, 10) || targetSubShotIndex;

        showToast(`镜头图生成已启动（镜头${startedSubShotIndex}）`, 'success');



        // 添加延迟纭繚鍚庣数据已提浜?

        await new Promise(resolve => setTimeout(resolve, 500));



        // 刷新镜头列表以显示状鎬?

        console.log('[生成镜头细化图片] 寮€始刷新镜头列琛?');

        const shotsResponse = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/shots`);

        console.log('[生成镜头细化图片] 响应鐘舵€?', shotsResponse.ok, shotsResponse.status);



        if (shotsResponse.ok) {

            const shotsData = await shotsResponse.json();

            console.log('[生成镜头细化图片] 获取到镜头数鎹紝鍏?', shotsData.length);



            // 妫€查获取到的数鎹腑鏄惁有detail_images_status

            const targetShot = shotsData.find(s => s.id === shotId);

            console.log('[生成镜头细化图片] 鐩爣镜头完整数据:', JSON.stringify(targetShot, null, 2));

            console.log('[生成镜头细化图片] 鐩爣镜头ID:', targetShot?.id, 'detail_images_status:', targetShot?.detail_images_status);



            APP_STATE.shots = shotsData;

            console.log('[生成镜头细化图片] 已更新APP_STATE.shots');



            // Track prompt task so polling detects completion even if AI responds faster than the shots refresh

            if (targetShot && targetShot.detail_images_status === 'processing') {

                APP_STATE.previousProcessingTasks.add(`${shotId}:detail_images`);

                console.log('[生成镜头细化图片] 已添加到杞任务:', `${shotId}:detail_images`);

            }



            renderStoryboardShotsGrid();

            console.log('[生成镜头细化图片] 已调用renderStoryboardShotsGrid()');

        } else {

            console.error('[生成镜头细化图片] 刷新失败，状态码:', shotsResponse.status);

        }



        // 鍚姩全局杞（会鑷姩妫€查所有任务包鎷暅头图锛?

        startVideoStatusPolling();



    } catch (error) {

        console.error('[生成镜头细化图片] 失败:', error);

        showToast(`生成失败: ${error.message}`, 'error');

    }

}



// 杞镜头细化图片鐘舵€?

async function pollDetailImagesStatus(shotId) {

    const pollInterval = setInterval(async () => {

        try {

            const response = await apiRequest(`/api/shots/${shotId}/detail-images`);

            const data = await response.json();



            const hasProcessing = data.detail_images.some(img => img.status === 'processing');

            const hasPending = data.detail_images.some(img => img.status === 'pending');

            const hasCompleted = data.detail_images.some(img => img.status === 'completed');

            const allCompleted = data.detail_images.every(img => img.status === 'completed');



            // 鍙湁当没有processing和pending鐘舵€佹椂，才认为任务完成

            if (!hasProcessing && !hasPending) {

                clearInterval(pollInterval);



                if (allCompleted) {

                    showToast('所有子镜头图片生成完成', 'success');

                } else if (hasCompleted) {

                    // 有成功有失败 -> 部分成功

                    showToast('镜头图片生成完成（部分子镜头失败）', 'warning');

                } else {

                    // 全部失败

                    showToast('镜头图片生成失败', 'error');

                }



                // 刷新镜头列表

                const shotsResponse = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/shots`);

                APP_STATE.shots = await shotsResponse.json();

                renderStoryboardShotsGrid();

            }

        } catch (error) {

            console.error('[杞镜头细化图片鐘舵€乚 失败:', error);

            clearInterval(pollInterval);

        }

    }, 3000); // 姣?秒轮璇竴娆?

}



// 打开细化图片查看器（左右切图 + 设封面）

async function openDetailImagesViewer(shotId) {

    try {

        try {

            const shotsRefresh = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/shots`);

            if (shotsRefresh.ok) {

                APP_STATE.shots = await shotsRefresh.json();

            }

        } catch (e) {

            // ignore

        }



        const response = await apiRequest(`/api/shots/${shotId}/detail-images`);

        if (!response.ok) {

            showToast('获取细化图片失败', 'error');

            return;

        }



        const data = await response.json();

        const detailImages = Array.isArray(data?.detail_images) ? data.detail_images : [];

        if (detailImages.length === 0) {

            showToast('该镜头还没有细化图片，请先点击“生成镜头图”按钮', 'info');

            return;

        }



        const viewerItems = [];

        detailImages.forEach(detail => {

            const imgs = Array.isArray(detail?.images) ? detail.images : [];

            imgs.forEach((url, idx) => {

                const imageUrl = String(url || '').trim();

                if (!imageUrl) {

                    return;

                }

                viewerItems.push({

                    image_url: imageUrl,

                    sub_shot_index: detail.sub_shot_index,

                    time_range: detail.time_range || '',

                    visual_text: detail.visual_text || '',

                    audio_text: detail.audio_text || '',

                    image_index: idx + 1,

                    image_count: imgs.length

                });

            });

        });



        if (viewerItems.length === 0) {

            const hasProcessing = detailImages.some(item => item?.status === 'processing' || item?.status === 'pending');

            if (hasProcessing) {

                showToast('细化图片正在生成中，请稍候', 'info');

            } else {

                showToast('细化图片生成失败，请重试', 'error');

            }

            return;

        }



        const preferredImageUrl = getShotImageViewerInitialUrl(
            APP_STATE.shots.find(s => s.id === shotId) || APP_STATE.currentShot || {},
            data
        );

        const coverImageUrl = String(data?.cover_image_url || '').trim();

        const initialIndex = Math.max(

            0,

            viewerItems.findIndex(item => item.image_url === preferredImageUrl)

        );

        showDetailImagesModal(shotId, viewerItems, initialIndex, coverImageUrl);

    } catch (error) {

        console.error('[打开细化图片查看器] 失败:', error);

        showToast(`打开失败: ${error.message}`, 'error');

    }

}



// 显示细化图片查看器模态框

function showDetailImagesModal(shotId, viewerItems, currentIndex, coverImageUrl = '') {

    const existingModal = document.getElementById('detailImagesViewerModal');

    if (existingModal) {

        existingModal.remove();

    }



    const items = Array.isArray(viewerItems) ? viewerItems : [];

    if (items.length === 0) {

        return;

    }



    const safeIndex = Math.max(0, Math.min(currentIndex, items.length - 1));

    const currentItem = items[safeIndex];

    const currentCoverUrl = String(coverImageUrl || '').trim();

    const isCurrentCover = Boolean(currentCoverUrl) && currentCoverUrl === currentItem.image_url;

    APP_STATE.detailImagesViewerState = {

        shotId,

        items,

        currentIndex: safeIndex,

        coverImageUrl: currentCoverUrl

    };



    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'detailImagesViewerModal';

    modal.innerHTML = `

        <div class="modal-content" style="max-width: 980px; padding: 24px;">

            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">

                <div style="display: flex; align-items: center; gap: 12px;">

                    <h2 style="margin: 0; font-size: 20px;">镜头图预览</h2>

                    <span style="color: #888; font-size: 16px;">${safeIndex + 1}/${items.length}</span>

                </div>

                <button class="modal-close" onclick="closeDetailImagesModal()">&times;</button>

            </div>



            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">

                <div style="font-size: 13px; color: #aaa;">

                    子镜头 ${escapeHtml(String(currentItem.sub_shot_index || ''))}

                    ${currentItem.time_range ? ` · ${escapeHtml(currentItem.time_range)}` : ''}

                    ${currentItem.image_count > 1 ? ` · 第${currentItem.image_index}/${currentItem.image_count}张` : ''}

                </div>

                <button class="${isCurrentCover ? 'secondary-button' : 'primary-button'}"

                        style="padding: 6px 12px; font-size: 12px;"

                        ${isCurrentCover ? 'disabled' : ''}

                        onclick="setDetailImageAsCover(${shotId}, '${encodeURIComponent(currentItem.image_url)}')">

                    ${isCurrentCover ? '当前封面镜头图' : '设为封面镜头图'}

                </button>

            </div>



            <div style="display: flex; gap: 20px; align-items: center;">

                <button class="detail-images-nav-btn ${items.length <= 1 ? 'disabled' : ''}"

                        onclick="navigateDetailImages(-1)"

                        ${items.length <= 1 ? 'disabled' : ''}>

                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">

                        <path d="M15 18l-6-6 6-6"/>

                    </svg>

                </button>



                <div style="flex: 1; display: flex; justify-content: center; align-items: flex-start;">

                    <div style="width: min(720px, 100%); min-height: 300px; background: #2a2a2a; border-radius: 8px; overflow: hidden; cursor: pointer; display: flex; align-items: center; justify-content: center; padding: 8px;"

                         onclick="openImageFullscreen('${escapeHtml(currentItem.image_url)}')">

                        <img src="${escapeHtml(currentItem.image_url)}"

                             style="max-width: 100%; max-height: 72vh; width: auto; height: auto; object-fit: contain;" />

                    </div>

                </div>



                <button class="detail-images-nav-btn ${items.length <= 1 ? 'disabled' : ''}"

                        onclick="navigateDetailImages(1)"

                        ${items.length <= 1 ? 'disabled' : ''}>

                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">

                        <path d="M9 18l6-6-6-6"/>

                    </svg>

                </button>

            </div>



            <div style="margin-top: 16px; padding: 14px; background: #2a2a2a; border-radius: 8px;">

                <div style="margin-bottom: 8px;">

                    <strong style="color: #888;">画面描述:</strong>

                    <span style="color: #fff;">${escapeHtml(currentItem.visual_text || '无')}</span>

                </div>

                <div>

                    <strong style="color: #888;">台词音效:</strong>

                    <span style="color: #fff;">${escapeHtml(currentItem.audio_text || '无')}</span>

                </div>

            </div>

        </div>



        <style>

            .detail-images-nav-btn {

                width: 48px;

                height: 48px;

                border: none;

                background: #3a3a3a;

                color: #fff;

                border-radius: 50%;

                cursor: pointer;

                display: flex;

                align-items: center;

                justify-content: center;

                transition: all 0.2s;

            }

            .detail-images-nav-btn:hover:not(.disabled) {

                background: #4a4a4a;

                transform: scale(1.08);

            }

            .detail-images-nav-btn.disabled {

                opacity: 0.3;

                cursor: not-allowed;

            }

        </style>

    `;



    document.body.appendChild(modal);

}



function navigateDetailImages(step) {

    const state = APP_STATE.detailImagesViewerState;

    if (!state || !Array.isArray(state.items) || state.items.length === 0) {

        return;

    }

    if (state.items.length <= 1) {

        return;

    }

    const len = state.items.length;

    const currentIndex = Number.parseInt(state.currentIndex, 10) || 0;

    const offset = Number.parseInt(step, 10) || 0;

    const nextIndex = (currentIndex + offset + len) % len;

    showDetailImagesModal(state.shotId, state.items, nextIndex, state.coverImageUrl);

}



async function setDetailImageAsCover(shotId, encodedImageUrl) {

    const imageUrl = decodeURIComponent(String(encodedImageUrl || '')).trim();

    if (!imageUrl) {

        return;

    }

    try {

        const response = await apiRequest(`/api/shots/${shotId}/detail-images/cover`, {

            method: 'PATCH',

            body: JSON.stringify({ image_url: imageUrl })

        });

        let result = null;

        try {

            result = await response.json();

        } catch (error) {

            result = null;

        }

        if (!response.ok) {

            throw new Error(result?.detail || '设置封面失败');

        }



        updateShotInState(shotId, {

            storyboard_image_path: imageUrl,

            storyboard_image_status: 'completed'

        });

        renderStoryboardShotsGrid();

        if (APP_STATE.currentShot && APP_STATE.currentShot.id === shotId) {

            renderStoryboardSidebar();

        }



        const state = APP_STATE.detailImagesViewerState;

        if (state && state.shotId === shotId) {

            showDetailImagesModal(state.shotId, state.items, state.currentIndex, imageUrl);

        }

        showToast('已设为封面镜头图', 'success');

    } catch (error) {

        console.error('Failed to set detail image cover:', error);

        showToast(`设置失败: ${error.message}`, 'error');

    }

}



// 关闭细化图片查看器

function closeDetailImagesModal() {

    APP_STATE.detailImagesViewerState = null;

    const modal = document.getElementById('detailImagesViewerModal');

    if (modal) {

        modal.remove();

    }

}



// 打开图片全屏查看

function openImageFullscreen(imageUrl) {

    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'imageFullscreenModal';

    modal.style.zIndex = '10001'; // 纭繚在其他模鎬佹之上

    modal.innerHTML = `

        <div class="modal-content" style="max-width: 95vw; max-height: 95vh; padding: 0; background: transparent; box-shadow: none;">

            <button class="modal-close" onclick="closeImageFullscreen()" style="position: absolute; top: 20px; right: 20px; z-index: 10002;">&times;</button>

            <img src="${escapeHtml(imageUrl)}" style="max-width: 100%; max-height: 95vh; object-fit: contain; cursor: pointer;" onclick="closeImageFullscreen()" />

        </div>

    `;



    document.body.appendChild(modal);

}



// 关闭图片全屏查看

function closeImageFullscreen() {

    const modal = document.getElementById('imageFullscreenModal');

    if (modal) {

        modal.remove();

    }

}



// ==================== 鑷畾义模鎬佹 ====================



// 鑷畾义确璁ゅ璇濇

function showConfirmDialog(message, onConfirm, onCancel = null) {

    const modal = document.createElement('div');

    modal.className = 'modal';

    modal.id = 'customConfirmModal';

    modal.innerHTML = `

        <div class="modal-content" style="max-width: 500px; padding: 24px;">

            <div style="margin-bottom: 24px; color: #fff; font-size: 14px; line-height: 1.6; white-space: pre-line;">

                ${message}

            </div>

            <div style="display: flex; justify-content: flex-end; gap: 10px;">

                <button class="secondary-button" onclick="closeConfirmDialog(false)">取消</button>

                <button class="primary-button" onclick="closeConfirmDialog(true)">确认</button>

            </div>

        </div>

    `;



    document.body.appendChild(modal);

    modal.style.display = 'flex';



    // 存储回调函数

    window._confirmCallback = { onConfirm, onCancel };

}



// 关闭纭对话妗?

function closeConfirmDialog(confirmed) {

    const modal = document.getElementById('customConfirmModal');

    if (modal) {

        modal.remove();

    }



    const callbacks = window._confirmCallback;

    if (callbacks) {

        if (confirmed && callbacks.onConfirm) {

            callbacks.onConfirm();

        } else if (!confirmed && callbacks.onCancel) {

            callbacks.onCancel();

        }

        window._confirmCallback = null;

    }

}



// 鑷畾义提绀哄璇濇

function showAlertDialog(message) {

    const modal = document.createElement('div');

    modal.className = 'modal';

    modal.id = 'customAlertModal';

    modal.innerHTML = `

        <div class="modal-content" style="max-width: 500px; padding: 24px;">

            <div style="margin-bottom: 24px; color: #fff; font-size: 14px; line-height: 1.6; white-space: pre-line;">

                ${message}

            </div>

            <div style="display: flex; justify-content: flex-end;">

                <button class="primary-button" onclick="closeAlertDialog()">OK</button>

            </div>

        </div>

    `;



    document.body.appendChild(modal);

    modal.style.display = 'flex';

}



// 关闭提示对话妗?

function closeAlertDialog() {

    const modal = document.getElementById('customAlertModal');

    if (modal) {

        modal.remove();

    }

}



function showVideoMessageModal(title, message) {

    const modal = document.createElement('div');

    modal.className = 'modal';

    modal.id = 'videoMessageModal';

    modal.style.display = 'flex';

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="closeVideoMessageModal()"></div>

        <div class="modal-content" style="max-width: 600px; padding: 24px; position: relative; z-index: 1;">

            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid #2a2a2a;">

                <h3 style="margin: 0; font-size: 18px; color: #fff;">${escapeHtml(title || '视频任务信息')}</h3>

                <button onclick="closeVideoMessageModal()" style="background: none; border: none; color: #888; font-size: 24px; cursor: pointer; padding: 0; line-height: 1;">&times;</button>

            </div>

            <div style="margin-bottom: 24px;">

                <textarea readonly style="width: 100%; min-height: 150px; padding: 12px; background: #1a1a1a; border: 1px solid #333; border-radius: 4px; color: #e0e0e0; font-size: 13px; line-height: 1.6; resize: vertical; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">${escapeHtml(message || '')}</textarea>

            </div>

            <div style="display: flex; justify-content: flex-end;">

                <button class="secondary-button" onclick="closeVideoMessageModal()">关闭</button>

            </div>

        </div>

    `;

    document.body.appendChild(modal);



    // ESC閿叧闂?

    const escHandler = (e) => {

        if (e.key === 'Escape') {

            closeVideoMessageModal();

            document.removeEventListener('keydown', escHandler);

        }

    };

    document.addEventListener('keydown', escHandler);

}



function showVideoErrorModal(errorMessage) {

    showVideoMessageModal('视频生成失败原因', errorMessage);

}



async function showVideoProcessingInfo(shotId) {

    try {

        const response = await apiRequest(`/api/shots/${shotId}/video-status-info`);

        const result = response ? await response.json() : {};

        if (!response || !response.ok) {

            throw new Error(result.detail || '获取任务信息失败');

        }



        const normalizedStatus = String(result.status || '').trim().toLowerCase();

        const errorText = String(result.error_message || '').trim();

        const infoText = normalizedStatus === 'failed'

            ? (errorText || String(result.info || '').trim() || '任务已失败，但服务商未返回更多信息。')

            : (String(result.info || '').trim()

                || errorText

                || '当前任务正在处理中，服务商暂未返回更多排队信息。');

        showVideoMessageModal(normalizedStatus === 'failed' ? '视频生成失败原因' : '视频生成中信息', infoText);

    } catch (error) {

        console.error('Failed to load video processing info:', error);

        showToast(`获取任务信息失败: ${error.message}`, 'error');

    }

}



// 关闭视频信息模态框

function closeVideoMessageModal() {

    const modal = document.getElementById('videoMessageModal');

    if (modal) {

        modal.remove();

    }

}



// ==================== 托管视频生成功能 ====================



let managedSessionPollingInterval = null;

let managedTaskPromptMap = {};



function isManagedSessionActiveStatus(status) {

    return status === 'running' || status === 'detached';

}



function getManagedToolbarHtml(managedSession) {

    const session = managedSession || { status: 'none', total_shots: 0, completed_shots: 0 };

    if (session.status === 'running') {

        return `

            <button class="primary-button storyboard-tool-button" onclick="stopManagedGeneration()">停止托管</button>

            <button class="secondary-button storyboard-tool-button" onclick="showManagedTaskDetails()">托管详情 (${session.completed_shots}/${session.total_shots})</button>

        `;

    }

    if (session.status === 'detached') {

        return `

            <span class="batch-generate-status managed-toolbar-status">上一批托管后台收尾中...</span>

            <button class="primary-button storyboard-tool-button" onclick="startManagedGeneration()">开始托管</button>

            <button class="secondary-button storyboard-tool-button" onclick="showManagedTaskDetails()">托管详情 (${session.completed_shots}/${session.total_shots})</button>

        `;

    }

    if (session.status === 'completed' || session.status === 'failed' || session.status === 'stopped') {

        return `

            <button class="primary-button storyboard-tool-button" onclick="startManagedGeneration()">开始托管</button>

            <button class="secondary-button storyboard-tool-button" onclick="showManagedTaskDetails()">托管详情 (${session.completed_shots}/${session.total_shots})</button>

        `;

    }

    return `<button class="primary-button storyboard-tool-button" onclick="startManagedGeneration()">开始托管</button>`;

}



function formatManagedTaskShotLabel(task) {

    if (!task || !(Number(task.shot_number) > 0)) {

        return '待生成';

    }

    const variantIndex = Number(task.variant_index || 0);

    return variantIndex > 0 ? `${task.shot_number}_${variantIndex}` : `${task.shot_number}`;

}



// 开始托管视频生成

async function startManagedGeneration() {

    if (!APP_STATE.shots || APP_STATE.shots.length === 0) {

        showToast('没有可生成的镜头', 'info');

        return;

    }



    // 筛选原始镜头

    const mainShots = APP_STATE.shots.filter(s => s.variant_index === 0);

    if (mainShots.length === 0) {

        showToast('没有可生成的镜头', 'info');

        return;

    }



    // 显示服务商选择和镜头选择弹窗

    const modal = document.createElement('div');

    modal.className = 'modal form-modal';

    modal.id = 'providerSelectModal';

    modal.style.display = 'flex';

    modal.innerHTML = `

        <div class="modal-content" style="max-width: 600px;">

            <div class="modal-header">

                <h3>开始托管视频生成</h3>

                <button class="modal-close" onclick="closeProviderModal()">&times;</button>

            </div>

            <div class="modal-body">

                <p style="color: #999; font-size: 13px; margin-bottom: 12px;">

                    托管模式会按“图/视频设置”自动生成视频。

                </p>



                <div style="margin-bottom: 12px; font-size: 12px; color: #aaa; border: 1px solid #2a2a2a; border-radius: 6px; padding: 10px; background: #0a0a0a;">

                    ${(() => {

                        const settings = getEpisodeStoryboardVideoSettings();

                        const price = getStoryboardVideoPrice(settings.model, settings.duration);

                        return `当前视频设置：<span style="color:#fff;">${escapeHtml(settings.model)} / ${escapeHtml(settings.aspect_ratio)} / ${settings.duration}s</span>

                            <span style="color:#666;">（服务商 ${escapeHtml(settings.provider)}，单价 ¥${escapeHtml(formatStoryboardVideoPrice(price))}）</span>`;

                    })()}

                </div>



                <div class="form-group" style="margin-bottom: 20px;">

                    <label class="form-label">每个镜头生成数量</label>

                    <div style="font-size: 12px; color: #aaa; border: 1px solid #2a2a2a; border-radius: 6px; padding: 10px; background: #0a0a0a;">

                        当前固定为 1 个视频。

                    </div>

                </div>



                <div class="form-group">

                    <label class="form-label">选择镜头</label>

                    <div style="display: flex; gap: 10px; margin-bottom: 10px;">

                        <button class="secondary-button" style="flex: 1;" onclick="selectAllManagedShots()">全选</button>

                        <button class="secondary-button" style="flex: 1;" onclick="unselectAllManagedShots()">取消全选</button>

                    </div>

                    <div id="managedShotsList" style="max-height: 200px; overflow-y: auto; border: 1px solid #2a2a2a; border-radius: 4px; padding: 10px; background: #0a0a0a;">

                        ${mainShots.map(shot => `

                            <label style="display: flex; align-items: center; gap: 8px; padding: 6px; cursor: pointer; border-radius: 2px; transition: background 0.2s;"

                                   onmouseover="this.style.background='#1a1a1a'"

                                   onmouseout="this.style.background='transparent'">

                                <input type="checkbox"

                                       class="managed-shot-checkbox"

                                       data-shot-id="${shot.id}"

                                       checked

                                       style="width: 16px; height: 16px; cursor: pointer;">

                                <span style="color: #fff; font-size: 13px;">镜头 ${shot.shot_number}</span>

                            </label>

                        `).join('')}

                    </div>

                </div>



                <div class="modal-form-actions">

                    <button class="secondary-button" onclick="closeProviderModal()">取消</button>

                    <button class="primary-button" onclick="confirmStartManaged()">开始托管</button>

                </div>

            </div>

        </div>

    `;



    document.body.appendChild(modal);



    // 点击背景关闭

    modal.addEventListener('click', (e) => {

        if (e.target === modal) {

            closeProviderModal();

        }

    });

}



// 关闭服务商选择弹窗

function closeProviderModal() {

    const modal = document.getElementById('providerSelectModal');

    if (modal) {

        modal.remove();

    }

}



// 全选托管镜头

function selectAllManagedShots() {

    document.querySelectorAll('.managed-shot-checkbox').forEach(cb => cb.checked = true);

}



// 取消全选托管镜头

function unselectAllManagedShots() {

    document.querySelectorAll('.managed-shot-checkbox').forEach(cb => cb.checked = false);

}



// 确认开始托管

async function confirmStartManaged() {

    const videoSettings = getEpisodeStoryboardVideoSettings();

    const provider = videoSettings.provider;



    const variantCount = 1;



    // 获取选中的镜头

    const selectedCheckboxes = document.querySelectorAll('.managed-shot-checkbox:checked');

    const selectedShotIds = Array.from(selectedCheckboxes).map(cb => parseInt(cb.dataset.shotId));



    if (selectedShotIds.length === 0) {

        showToast('请至少选择一个镜头', 'warning');

        return;

    }



    // 关闭provider选择弹窗

    closeProviderModal();



    // 显示确认对话框

    const totalVideos = selectedShotIds.length * variantCount;

    showConfirmDialog(

        `确认开始托管视频生成？\n选中 ${selectedShotIds.length} 个镜头，每个镜头生成 ${variantCount} 个视频。\n总共将生成 ${totalVideos} 个视频。\n设置: ${videoSettings.model} / ${videoSettings.aspect_ratio} / ${videoSettings.duration}s / ${provider}\n镜头如单独设置过时长，会优先使用镜头时长。`,

        async () => {

            // 用户确认，开始托管

            try {

                const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/start-managed-generation`, {

                    method: 'POST',

                    body: JSON.stringify({

                        provider: provider,

                        model: videoSettings.model,

                        aspect_ratio: videoSettings.aspect_ratio,

                        duration: videoSettings.duration,

                        resolution_name: videoSettings.resolution_name,

                        shot_ids: selectedShotIds,

                        variant_count: variantCount

                    })

                });



                const result = await response.json();

                if (!response.ok) {

                    throw new Error(result.detail || result.message || '开始托管失败');

                }

                showAlertDialog(result.message);



                // 启动轮询

                startManagedSessionPolling();



                // 刷新界面

                await loadStoryboardStep();

            } catch (err) {

                showAlertDialog('开始托管失败: ' + err.message);

            }

        }

    );

}



// 停止托管视频生成

async function stopManagedGeneration() {

    showConfirmDialog(

        '确认取消当前托管？已预留的结果槽位会继续在后台完成，不会向上游发送取消请求。',

        async () => {

            try {

                const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/stop-managed-generation`, {

                    method: 'POST'

                });



                const result = await response.json();

                showAlertDialog(result.message);



                // 停止轮询

                stopManagedSessionPolling();



                // 刷新界面

                await loadStoryboardStep();

            } catch (err) {

                showAlertDialog('停止托管失败: ' + err.message);

            }

        }

    );

}



// 显示托管任务详情

async function showManagedTaskDetails() {

    if (!APP_STATE.managedSession || !APP_STATE.managedSession.session_id) {

        showAlertDialog('没有托管会话');

        return;

    }



    try {

        const response = await apiRequest(`/api/managed-sessions/${APP_STATE.managedSession.session_id}/tasks`);

        const tasks = await response.json();

        managedTaskPromptMap = Object.fromEntries(

            tasks.map(task => [String(task.id), String(task.prompt_text || '')])

        );



        // 创建弹窗

        let modal = document.getElementById('managedTaskDetailsModal');

        if (!modal) {

            modal = document.createElement('div');

            modal.id = 'managedTaskDetailsModal';

            modal.className = 'modal';

            document.body.appendChild(modal);

        }



        // 构建任务列表 HTML

        const tasksByStatus = {

            pending: tasks.filter(t => t.status === 'pending'),

            processing: tasks.filter(t => t.status === 'processing'),

            completed: tasks.filter(t => t.status === 'completed'),

            failed: tasks.filter(t => t.status === 'failed')

        };



        const statusLabels = {

            pending: '等待中',

            processing: '生成中',

            completed: '已完成',

            failed: '失败'

        };



        let filterButtons = ['all', 'pending', 'processing', 'completed', 'failed']

            .map(status => `<button class="secondary-button" onclick="filterManagedTasks('${status}')">${status === 'all' ? '全部' : statusLabels[status]} (${status === 'all' ? tasks.length : tasksByStatus[status]?.length || 0})</button>`)

            .join('');



        let tasksHtml = tasks.map(task => {

            const statusClass = task.status;

            const shotLabel = formatManagedTaskShotLabel(task);

            const originalShotLabel = task.original_shot_number > 0 ? task.original_shot_number : '-';

            const createdTime = formatBackendUtcToBeijing(task.created_at);

            const completedTime = task.completed_at ? formatBackendUtcToBeijing(task.completed_at) : '-';



            return `

                <tr class="managed-task-row" data-status="${task.status}">

                    <td style="padding: 8px;">

                        ${task.prompt_text

                            ? `<button class="link-button" style="background:none;border:none;color:#7cc4ff;cursor:pointer;padding:0;font:inherit;" onclick="showManagedTaskPrompt(${task.id})">${task.id}</button>`

                            : task.id}

                    </td>

                    <td style="padding: 8px;">${originalShotLabel}</td>

                    <td style="padding: 8px;">${shotLabel}</td>

                    <td style="padding: 8px;"><span class="status-badge status-${statusClass}">${statusLabels[task.status]}</span></td>

                    <td style="padding: 8px; min-width: 120px; max-width: 180px; overflow: hidden; text-overflow: ellipsis; font-family: monospace; font-size: 11px;" title="${task.shot_stable_id || ''}">${task.shot_stable_id || '-'}</td>

                    <td style="padding: 8px; min-width: 250px; max-width: 400px; overflow: hidden; text-overflow: ellipsis; font-family: monospace; font-size: 11px;" title="${escapeHtml(task.task_id || '')}">${escapeHtml(task.task_id || '-')}</td>

                    <td style="padding: 8px;">${createdTime}</td>

                    <td style="padding: 8px;">${completedTime}</td>

                    <td style="padding: 8px; max-width: 200px; overflow: hidden; text-overflow: ellipsis;">${task.error_message || '-'}</td>

                </tr>

            `;

        }).join('');



        modal.innerHTML = `

            <div class="modal-backdrop" onclick="closeManagedTaskDetailsModal()"></div>

            <div class="modal-content" style="max-width: 90vw; max-height: 90vh;">

                <div style="padding: 20px 24px; background: linear-gradient(180deg, #1a1a1a 0%, #0f0f0f 100%); border-bottom: 1px solid #333;">

                    <h2 style="margin: 0; color: #fff; font-size: 20px;">托管任务详情</h2>

                    <p style="margin: 8px 0 0 0; color: #888; font-size: 13px;">

                        总计: ${tasks.length} 个任务 |

                        已完成: ${tasksByStatus.completed.length} |

                        进行中: ${tasksByStatus.processing.length} |

                        失败: ${tasksByStatus.failed.length}

                    </p>

                </div>

                <div style="padding: 16px 24px; border-bottom: 1px solid #333;">

                    ${filterButtons}

                </div>

                <div style="flex: 1; overflow: auto; padding: 16px 24px;">

                    <table style="width: 100%; border-collapse: collapse; color: #ddd;">

                        <thead>

                            <tr style="border-bottom: 1px solid #444;">

                                <th style="padding: 8px; text-align: left; width: 80px;">托管任务ID</th>

                                <th style="padding: 8px; text-align: left; width: 100px;">原始镜头</th>

                                <th style="padding: 8px; text-align: left; width: 120px;">结果镜头</th>

                                <th style="padding: 8px; text-align: left; width: 100px;">状态</th>

                                <th style="padding: 8px; text-align: left; min-width: 120px;">Stable ID</th>

                                <th style="padding: 8px; text-align: left; min-width: 250px;">上游任务ID</th>

                                <th style="padding: 8px; text-align: left; width: 160px;">创建时间</th>

                                <th style="padding: 8px; text-align: left; width: 160px;">完成时间</th>

                                <th style="padding: 8px; text-align: left;">错误信息</th>

                            </tr>

                        </thead>

                        <tbody>

                            ${tasksHtml}

                        </tbody>

                    </table>

                </div>

                <div style="padding: 16px 24px; border-top: 1px solid #333; display: flex; justify-content: flex-end;">

                    <button class="secondary-button" onclick="closeManagedTaskDetailsModal()">关闭</button>

                </div>

            </div>

        `;



        modal.style.display = 'flex';

    } catch (err) {

        showAlertDialog('加载托管详情失败: ' + err.message);

    }

}



// 关闭托管任务详情弹窗

function closeManagedTaskDetailsModal() {

    const modal = document.getElementById('managedTaskDetailsModal');

    if (modal) {

        modal.style.display = 'none';

    }

}



function showManagedTaskPrompt(taskId) {

    const promptText = String(managedTaskPromptMap[String(taskId)] || '').trim();

    if (!promptText) {

        showAlertDialog('当前托管任务没有可展示的完整提示词');

        return;

    }

    showVideoMessageModal(`托管任务 ${taskId} 完整提示词`, promptText);

}



// 筛选托管任务

function filterManagedTasks(status) {

    const rows = document.querySelectorAll('.managed-task-row');

    rows.forEach(row => {

        if (status === 'all' || row.getAttribute('data-status') === status) {

            row.style.display = '';

        } else {

            row.style.display = 'none';

        }

    });

}



// 启动托管会话状态轮询

function startManagedSessionPolling() {

    if (managedSessionPollingInterval) {

        return; // 已经在轮询中

    }



    console.log('[托管轮询] 启动托管会话状态轮询');



    // 立即执行一次

    updateManagedSessionStatus();



    // 每 5 秒轮询一次

    managedSessionPollingInterval = setInterval(updateManagedSessionStatus, MANAGED_SESSION_POLL_INTERVAL_MS);

}



// 停止托管会话状态轮询

function stopManagedSessionPolling() {

    if (managedSessionPollingInterval) {

        clearInterval(managedSessionPollingInterval);

        managedSessionPollingInterval = null;

        console.log('[托管轮询] 停止托管会话状态轮询');

    }

}



// 更新托管会话状态

async function updateManagedSessionStatus() {

    if (!APP_STATE.currentEpisode) {

        return;

    }



    return withPollingGuard('managedSessionStatus', async () => {

        try {

            const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/managed-session-status`);

            const sessionStatus = await response.json();



            APP_STATE.managedSession = sessionStatus;



            // 如果会话已结束，停止轮询

            if (sessionStatus.status === 'completed' || sessionStatus.status === 'failed' || sessionStatus.status === 'stopped') {

                stopManagedSessionPolling();

                const statusText = sessionStatus.status === 'completed'

                    ? '已完成'

                    : (sessionStatus.status === 'failed' ? '已结束（部分失败）' : '已停止');

                alert(`托管${statusText}`);

                await loadStoryboardStep(); // 刷新界面

            } else if (isManagedSessionActiveStatus(sessionStatus.status)) {

                // 更新工具栏按钮

                updateManagedToolbarButtons();

            }

        } catch (err) {

            console.error('[托管轮询] 更新托管会话状态失败:', err);

        }

    });

}



// 更新托管工具栏按钮

function updateManagedToolbarButtons() {

    const toolsRight = document.querySelector('.storyboard-tools-right');

    if (!toolsRight) {

        return;

    }



    const managedSession = APP_STATE.managedSession || { status: 'none', total_shots: 0, completed_shots: 0 };

    const managedButtonHtml = getManagedToolbarHtml(managedSession);



    // 查找并替换托管按钮

    const buttons = Array.from(toolsRight.querySelectorAll('button'));



    // 找到第一个托管相关按钮的位置

    let firstManagedButtonIndex = -1;

    for (let i = 0; i < buttons.length; i++) {

        const btnText = buttons[i].textContent;

        if (btnText.includes('托管') || btnText.includes('开始托管') || btnText.includes('停止托管')) {

            firstManagedButtonIndex = i;

            break;

        }

    }



    if (firstManagedButtonIndex === -1) {

        return; // 没找到托管按钮，不处理

    }



    // 移除所有托管相关按钮

    buttons.forEach(btn => {

        const btnText = btn.textContent;

        if (btnText.includes('托管') || btnText.includes('开始托管') || btnText.includes('停止托管')) {

            btn.remove();

        }

    });

    toolsRight.querySelectorAll('.managed-toolbar-status').forEach(node => node.remove());



    // 在第一个托管按钮的位置插入新的按钮

    const tempDiv = document.createElement('div');

    tempDiv.innerHTML = managedButtonHtml;



    const referenceButton = buttons[firstManagedButtonIndex + 1]; // 获取下一个按钮作为参考点

    if (referenceButton && referenceButton.parentNode === toolsRight) {

        // 在参考按钮之前插入

        while (tempDiv.firstChild) {

            toolsRight.insertBefore(tempDiv.firstChild, referenceButton);

        }

    } else {

        // 如果没有参考按钮，添加到开头

        while (tempDiv.firstChild) {

            toolsRight.insertBefore(tempDiv.firstChild, toolsRight.firstChild);

        }

    }

}



/* ==================== 配音表 V2（后端接入版） ==================== */



const VOICEOVER_V2_METHODS = ['与音色参考音频相同', '使用情感向量控制', '使用情感描述文本控制', '使用情感参考音频'];

const VOICEOVER_V2_VECTOR_DIMS = [

    { key: 'joy', label: '喜' },

    { key: 'anger', label: '怒' },

    { key: 'sadness', label: '哀' },

    { key: 'fear', label: '惧' },

    { key: 'disgust', label: '厌恶' },

    { key: 'depression', label: '低落' },

    { key: 'surprise', label: '惊喜' },

    { key: 'neutral', label: '平静' }

];



function voiceoverV2Id(prefix) {

    return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;

}



function voiceoverV2ScriptKey() {

    return String(APP_STATE.currentScript || 'global');

}



function voiceoverV2EpisodeKey() {

    return String(APP_STATE.currentEpisode || 'global');

}



function voiceoverV2DefaultVoiceRef() {

    return {

        id: 'voice_ref_default_female_1',

        name: '女声1',

        fileName: 'test.mp3',

        url: '',

        localPath: '',

        createdAt: new Date().toISOString()

    };

}



function voiceoverV2DefaultFemaleVoiceRefId() {

    const scriptState = voiceoverV2EnsureScriptState();

    const refs = Array.isArray(scriptState?.voiceReferences) ? scriptState.voiceReferences : [];

    const byId = refs.find(item => String(item?.id || '').trim() === 'voice_ref_default_female_1');

    if (byId?.id) return String(byId.id);

    const byName = refs.find(item => String(item?.name || '').trim() === '女声1');

    if (byName?.id) return String(byName.id);

    return String(refs[0]?.id || 'voice_ref_default_female_1');

}



function voiceoverV2NormalizeSettingTemplate(item) {

    const source = item && typeof item === 'object' ? item : {};

    const id = String(source.id || '').trim();

    const name = String(source.name || '').trim();

    if (!id || !name) return null;

    const settingsRaw = source.settings && typeof source.settings === 'object' ? source.settings : {};

    const methodRaw = String(settingsRaw.emotion_control_method || settingsRaw.emotionControlMethod || '与音色参考音频相同');

    return {

        id,

        name,

        settings: {

            emotionControlMethod: VOICEOVER_V2_METHODS.includes(methodRaw) ? methodRaw : '与音色参考音频相同',

            voiceReferenceId: String(settingsRaw.voice_reference_id || settingsRaw.voiceReferenceId || ''),

            vectorPresetId: String(settingsRaw.vector_preset_id || settingsRaw.vectorPresetId || ''),

            emotionAudioPresetId: String(settingsRaw.emotion_audio_preset_id || settingsRaw.emotionAudioPresetId || ''),

            vectorConfig: voiceoverV2NormalizeVectorConfig(settingsRaw.vector_config || settingsRaw.vectorConfig)

        },

        createdAt: String(source.created_at || source.createdAt || ''),

        updatedAt: String(source.updated_at || source.updatedAt || '')

    };

}



function voiceoverV2NormalizeSharedState(shared) {

    const source = shared && typeof shared === 'object' ? shared : {};

    return {

        voiceReferences: Array.isArray(source.voice_references) ? source.voice_references.map(item => ({

            id: String(item.id || ''),

            name: String(item.name || ''),

            fileName: String(item.file_name || ''),

            url: String(item.url || ''),

            localPath: String(item.local_path || ''),

            createdAt: String(item.created_at || '')

        })).filter(item => item.id && item.name) : [],

        vectorPresets: Array.isArray(source.vector_presets) ? source.vector_presets.map(item => ({

            id: String(item.id || ''),

            name: String(item.name || ''),

            description: String(item.description || ''),

            vectorConfig: voiceoverV2NormalizeVectorConfig(item.vector_config),

            createdAt: String(item.created_at || '')

        })).filter(item => item.id && item.name) : [],

        emotionAudioPresets: Array.isArray(source.emotion_audio_presets) ? source.emotion_audio_presets.map(item => ({

            id: String(item.id || ''),

            name: String(item.name || ''),

            description: String(item.description || ''),

            fileName: String(item.file_name || ''),

            url: String(item.url || ''),

            localPath: String(item.local_path || ''),

            createdAt: String(item.created_at || '')

        })).filter(item => item.id && item.name) : [],

        settingTemplates: Array.isArray(source.setting_templates)

            ? source.setting_templates.map(item => voiceoverV2NormalizeSettingTemplate(item)).filter(Boolean)

            : []

    };

}



function voiceoverV2ApplySharedState(shared) {

    const key = voiceoverV2ScriptKey();

    APP_STATE.voiceoverUiByScript = APP_STATE.voiceoverUiByScript || {};

    APP_STATE.voiceoverUiByScript[key] = voiceoverV2NormalizeSharedState(shared);

    return APP_STATE.voiceoverUiByScript[key];

}



function voiceoverV2NormalizeVectorConfig(config) {

    const source = config && typeof config === 'object' ? config : {};

    const normalized = {

        weight: Number.isFinite(Number(source.weight)) ? Math.max(0, Math.min(1, Number(source.weight))) : 0.65

    };

    VOICEOVER_V2_VECTOR_DIMS.forEach(item => {

        const value = Number(source[item.key]);

        normalized[item.key] = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;

    });

    if (normalized.neutral === 0 && Object.values(normalized).every(v => Number(v) === 0 || Number.isNaN(Number(v)))) {

        normalized.neutral = 1;

    }

    return normalized;

}



function voiceoverV2EnsureScriptState() {

    const key = voiceoverV2ScriptKey();

    APP_STATE.voiceoverUiByScript = APP_STATE.voiceoverUiByScript || {};

    if (!APP_STATE.voiceoverUiByScript[key] || typeof APP_STATE.voiceoverUiByScript[key] !== 'object') {

        APP_STATE.voiceoverUiByScript[key] = {

            voiceReferences: [],

            vectorPresets: [],

            emotionAudioPresets: [],

            settingTemplates: []

        };

    }

    const state = APP_STATE.voiceoverUiByScript[key];

    if (!Array.isArray(state.voiceReferences)) state.voiceReferences = [];

    if (!Array.isArray(state.vectorPresets)) state.vectorPresets = [];

    if (!Array.isArray(state.emotionAudioPresets)) state.emotionAudioPresets = [];

    if (!Array.isArray(state.settingTemplates)) state.settingTemplates = [];

    // 后端为主，前端不再把配音共享资源持久化到localStorage

    return state;

}



function voiceoverV2EnsureEpisodeSettings() {

    // 兼容旧调用：现已不使用前端本地行配置

    return {};

}



function voiceoverV2LineKey(line, index) {

    return String(line?.lineId || `${line?.shotNumber || 0}_${line?.type || 'unknown'}_${index}`);

}



function voiceoverV2DefaultLineTts(scriptState) {

    const defaultVoiceRefId = scriptState?.voiceReferences?.[0]?.id || '';

    return {

        emotionControlMethod: '与音色参考音频相同',

        voiceReferenceId: defaultVoiceRefId,

        vectorPresetId: '',

        emotionAudioPresetId: '',

        vectorConfig: voiceoverV2NormalizeVectorConfig({ neutral: 1, weight: 0.65 }),

        generatedAudios: [],

        generateStatus: 'idle',

        generateError: '',

        latestTaskId: ''

    };

}



function voiceoverV2NormalizeLines(rawLines) {

    const scriptState = voiceoverV2EnsureScriptState();

    return (rawLines || []).map((line, index) => {

        const defaults = voiceoverV2DefaultLineTts(scriptState);

        const rawTts = line && typeof line.tts === 'object' ? line.tts : {};

        const generatedAudiosRaw = Array.isArray(rawTts.generated_audios) ? rawTts.generated_audios : rawTts.generatedAudios;

        const generatedAudios = Array.isArray(generatedAudiosRaw) ? generatedAudiosRaw : [];

        const statusRaw = String(rawTts.generate_status || rawTts.generateStatus || 'idle').toLowerCase();

        const generateStatus = ['idle', 'pending', 'processing', 'completed', 'failed'].includes(statusRaw) ? statusRaw : 'idle';

        const methodRaw = String(rawTts.emotion_control_method || rawTts.emotionControlMethod || defaults.emotionControlMethod);

        const tts = {

            ...defaults,

            emotionControlMethod: VOICEOVER_V2_METHODS.includes(methodRaw) ? methodRaw : defaults.emotionControlMethod,

            voiceReferenceId: String(rawTts.voice_reference_id || rawTts.voiceReferenceId || defaults.voiceReferenceId || ''),

            vectorPresetId: String(rawTts.vector_preset_id || rawTts.vectorPresetId || ''),

            emotionAudioPresetId: String(rawTts.emotion_audio_preset_id || rawTts.emotionAudioPresetId || ''),

            vectorConfig: voiceoverV2NormalizeVectorConfig(rawTts.vector_config || rawTts.vectorConfig || defaults.vectorConfig),

            generatedAudios: generatedAudios.map(item => ({

                id: String(item.id || `tts_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`),

                name: String(item.name || '生成结果'),

                url: String(item.url || ''),

                taskId: String(item.task_id || item.taskId || ''),

                createdAt: String(item.created_at || item.createdAt || ''),

                status: String(item.status || 'completed')

            })).filter(item => item.url),

            generateStatus,

            generateError: String(rawTts.generate_error || rawTts.generateError || ''),

            latestTaskId: String(rawTts.latest_task_id || rawTts.latestTaskId || '')

        };

        if (tts.voiceReferenceId && !scriptState.voiceReferences.some(item => item.id === tts.voiceReferenceId)) {

            tts.voiceReferenceId = scriptState.voiceReferences[0]?.id || '';

        }

        if (tts.vectorPresetId && !scriptState.vectorPresets.some(item => item.id === tts.vectorPresetId)) {

            tts.vectorPresetId = '';

        }

        if (tts.emotionAudioPresetId && !scriptState.emotionAudioPresets.some(item => item.id === tts.emotionAudioPresetId)) {

            tts.emotionAudioPresetId = '';

        }

        return {

            ...line,

            lineId: String(line.lineId || line.line_id || voiceoverV2LineKey(line, index)),

            tts

        };

    });

}



function voiceoverV2PersistLineSettings() {

    // 兼容旧调用：行级配置直接保存在voiceover_data，不再使用本地缓存

}



function voiceoverV2SaveStateText() {

    if (APP_STATE.voiceoverSaveState === 'saving') return '自动保存中...';

    if (APP_STATE.voiceoverSaveState === 'saved') return APP_STATE.voiceoverLastSavedAt ? `已自动保存 ${APP_STATE.voiceoverLastSavedAt}` : '已自动保存';

    if (APP_STATE.voiceoverSaveState === 'error') return '自动保存失败';

    return '每行失焦自动保存';

}



function voiceoverV2UpdateSaveHint() {

    const hint = document.getElementById('voiceoverAutoSaveHint');

    if (hint) hint.textContent = voiceoverV2SaveStateText();

}



function voiceoverV2ScheduleAutoSave() {

    APP_STATE.voiceoverSaveState = 'saving';

    voiceoverV2UpdateSaveHint();

    if (APP_STATE.voiceoverAutosaveTimer) clearTimeout(APP_STATE.voiceoverAutosaveTimer);

    APP_STATE.voiceoverAutosaveTimer = setTimeout(() => {

        saveVoiceoverTable({ silent: true, auto: true });

    }, 500);

}



async function loadVoiceoverTableStep() {

    const container = document.getElementById('creationContainer');

    setContentTightBottom(false);

    container.innerHTML = '<div class="loading">加载中...</div>';

    if (APP_STATE.voiceoverStatusPollingInterval) {

        clearInterval(APP_STATE.voiceoverStatusPollingInterval);

        APP_STATE.voiceoverStatusPollingInterval = null;

    }



    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/detailed-storyboard`);

        const data = await response.json();

        if (!response.ok) throw new Error(data.detail || '加载失败');

        const shots = data.shots || [];

        const subjects = data.subjects || [];

        const shared = data.tts_shared || {};

        voiceoverV2ApplySharedState(shared);



        const lines = [];

        let lineNumber = 1;



        shots.forEach(shot => {

            if (shot.narration && typeof shot.narration === 'object') {

                lines.push({

                    lineNumber: lineNumber++,

                    shotNumber: shot.shot_number,

                    text: shot.narration.text || '',

                    speaker: shot.narration.speaker || '',

                    gender: shot.narration.gender || '未知',

                    emotion: shot.narration.emotion || '',

                    type: 'narration',

                    lineId: String(shot.narration.line_id || `shot_${shot.shot_number}_narration`),

                    tts: shot.narration.tts || {}

                });

            }

            if (Array.isArray(shot.dialogue)) {

                shot.dialogue.forEach((item, dialogueIndex) => {

                    lines.push({

                        lineNumber: lineNumber++,

                        shotNumber: shot.shot_number,

                        text: item.text || '',

                        speaker: item.speaker || '',

                        gender: item.gender || '未知',

                        emotion: item.emotion || '',

                        type: 'dialogue',

                        target: item.target || null,

                        lineId: String(item.line_id || `shot_${shot.shot_number}_dialogue_${dialogueIndex + 1}`),

                        tts: item.tts || {}

                    });

                });

            }

        });



        window.voiceoverSubjectsData = subjects;

        window.voiceoverLinesData = voiceoverV2NormalizeLines(lines);

        APP_STATE.voiceoverSaveState = 'idle';

        renderVoiceoverTable(window.voiceoverLinesData, subjects);

        voiceoverV2UpdateSaveHint();

        voiceoverV2UpdateStatusPolling();

    } catch (error) {

        console.error('Failed to load voiceover table:', error);

        container.innerHTML = '<div class="empty-state">加载失败: ' + error.message + '</div>';

    }

}



function renderVoiceoverTable(lines, subjects) {

    const container = document.getElementById('creationContainer');

    const hasData = Array.isArray(lines) && lines.length > 0;

    const characters = (subjects || []).filter(item => item.type === '角色');

    if (!hasData) {

        container.innerHTML = `

            <div class="storyboard-table-container">

                <div class="empty-state" style="padding: 60px 20px; text-align: center;">

                    <p style="font-size: 16px; color: #999; margin-bottom: 20px;">暂无配音数据</p>

                    <p style="font-size: 14px; color: #666; margin-bottom: 30px;">请先在“详细分镜”页面生成详细分镜数据</p>

                    <button class="primary-button" onclick="switchStep(2)">前往详细分镜</button>

                </div>

            </div>

        `;

        return;

    }



    container.innerHTML = `

        <div class="storyboard-table-container voiceover-v2-container">

            <div class="voiceover-v2-toolbar">

                <div class="voiceover-v2-toolbar-actions">

                    <button class="secondary-button voiceover-mini-btn" onclick="voiceoverV2OpenBatchTemplateModal()">批量设置</button>

                    <button class="secondary-button voiceover-mini-btn" onclick="voiceoverV2GenerateAll()">全表生成</button>

                </div>

                <span class="voiceover-v2-toolbar-hint" id="voiceoverAutoSaveHint">${escapeHtml(voiceoverV2SaveStateText())}</span>

            </div>

            <div class="voiceover-v2-list">

                ${lines.map((line, index) => voiceoverV2BuildLineCard(line, index, characters)).join('')}

            </div>

        </div>

    `;

    voiceoverV2InitCustomPlayers();

}



function voiceoverV2Refresh() {

    const listBefore = document.querySelector('.voiceover-v2-list');

    const listScrollTop = listBefore ? listBefore.scrollTop : null;

    const pageScrollTop = window.scrollY || document.documentElement.scrollTop || 0;



    renderVoiceoverTable(window.voiceoverLinesData || [], window.voiceoverSubjectsData || []);



    const listAfter = document.querySelector('.voiceover-v2-list');

    if (listAfter && Number.isFinite(listScrollTop)) {

        listAfter.scrollTop = listScrollTop;

    }

    if (Number.isFinite(pageScrollTop)) {

        window.scrollTo(0, pageScrollTop);

    }

    voiceoverV2UpdateSaveHint();

}



function voiceoverV2HasActiveGeneration() {

    return Array.isArray(window.voiceoverLinesData) && window.voiceoverLinesData.some(line => {

        const status = String(line?.tts?.generateStatus || '').toLowerCase();

        return status === 'pending' || status === 'processing';

    });

}



async function voiceoverV2PollStatusOnce() {

    if (!APP_STATE.currentEpisode || !Array.isArray(window.voiceoverLinesData)) return;

    return withPollingGuard('voiceoverTtsStatus', async () => {

        try {

            const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover/tts-status`);

            if (!response?.ok) return;

            const data = await response.json();

            const states = Array.isArray(data.line_states) ? data.line_states : [];

            const stateMap = new Map();

            states.forEach(item => {

                const key = String(item?.line_id || '');

                if (!key) return;

                stateMap.set(key, item.tts || {});

            });



            let changed = false;

            window.voiceoverLinesData.forEach(line => {

                const raw = stateMap.get(String(line.lineId || ''));

                if (!raw) return;

                const next = voiceoverV2NormalizeLines([{ ...line, tts: raw }])[0]?.tts;

                if (!next) return;

                const prevJson = JSON.stringify(line.tts || {});

                const nextJson = JSON.stringify(next);

                if (prevJson !== nextJson) {

                    line.tts = next;

                    changed = true;

                }

            });



            if (changed) {

                voiceoverV2Refresh();

            }



            if (!voiceoverV2HasActiveGeneration() && APP_STATE.voiceoverStatusPollingInterval) {

                clearInterval(APP_STATE.voiceoverStatusPollingInterval);

                APP_STATE.voiceoverStatusPollingInterval = null;

            }

        } catch (error) {

            console.error('[voiceoverV2PollStatusOnce] failed:', error);

        }

    });

}



function voiceoverV2UpdateStatusPolling() {

    if (voiceoverV2HasActiveGeneration()) {

        if (!APP_STATE.voiceoverStatusPollingInterval) {

            APP_STATE.voiceoverStatusPollingInterval = setInterval(voiceoverV2PollStatusOnce, VOICEOVER_STATUS_POLL_INTERVAL_MS);

        }

    } else if (APP_STATE.voiceoverStatusPollingInterval) {

        clearInterval(APP_STATE.voiceoverStatusPollingInterval);

        APP_STATE.voiceoverStatusPollingInterval = null;

    }

}



function voiceoverV2BuildVectorSummary(line) {

    const vector = voiceoverV2NormalizeVectorConfig(line.tts?.vectorConfig);

    const active = VOICEOVER_V2_VECTOR_DIMS

        .filter(item => Number(vector[item.key]) > 0)

        .map(item => `${item.label}${Number(vector[item.key]).toFixed(2)}`);

    const dims = active.length > 0 ? active.join(' / ') : '未设置';

    return `权重 ${Number(vector.weight).toFixed(2)} · ${dims}`;

}



function voiceoverV2FormatAudioTime(seconds) {

    const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;

    const mm = Math.floor(safe / 60);

    const ss = Math.floor(safe % 60);

    return `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;

}



function voiceoverV2InitCustomPlayers() {

    document.querySelectorAll('.voiceover-custom-player').forEach(player => {

        if (player.dataset.bound === '1') return;

        player.dataset.bound = '1';



        const audio = player.querySelector('audio');

        const playBtn = player.querySelector('.voiceover-player-play');

        const slider = player.querySelector('.voiceover-player-slider');

        const timeEl = player.querySelector('.voiceover-player-time');

        if (!audio || !playBtn || !slider || !timeEl) return;



        const updateUi = () => {

            const duration = Number.isFinite(audio.duration) ? audio.duration : 0;

            const current = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;

            slider.value = duration > 0 ? String(Math.round((current / duration) * 1000)) : '0';

            timeEl.textContent = `${voiceoverV2FormatAudioTime(current)} / ${voiceoverV2FormatAudioTime(duration)}`;

            playBtn.textContent = audio.paused ? '播放' : '暂停';

        };



        playBtn.addEventListener('click', async () => {

            try {

                if (audio.paused) {

                    document.querySelectorAll('.voiceover-custom-player audio').forEach(other => {

                        if (other !== audio) other.pause();

                    });

                    await audio.play();

                } else {

                    audio.pause();

                }

            } catch (error) {

                console.error('[voiceoverV2 custom player] play failed:', error);

            } finally {

                updateUi();

            }

        });



        slider.addEventListener('input', () => {

            const duration = Number.isFinite(audio.duration) ? audio.duration : 0;

            if (duration <= 0) return;

            const ratio = Number(slider.value) / 1000;

            if (!Number.isFinite(ratio)) return;

            audio.currentTime = Math.max(0, Math.min(duration, ratio * duration));

            updateUi();

        });



        audio.addEventListener('loadedmetadata', updateUi);

        audio.addEventListener('timeupdate', updateUi);

        audio.addEventListener('pause', updateUi);

        audio.addEventListener('play', updateUi);

        audio.addEventListener('ended', updateUi);



        updateUi();

    });

}



function voiceoverV2SoftDeleteAudio(lineIndex, audioId) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const line = window.voiceoverLinesData[lineIndex];

    if (!line?.tts || !Array.isArray(line.tts.generatedAudios)) return;

    const targetId = String(audioId || '').trim();

    if (!targetId) return;



    let changed = false;

    line.tts.generatedAudios = line.tts.generatedAudios.map(item => {

        if (String(item?.id || '') !== targetId) return item;

        changed = true;

        return {

            ...item,

            status: 'deleted'

        };

    });



    if (!changed) return;

    voiceoverV2ScheduleAutoSave();

    voiceoverV2Refresh();

    showToast('音频已删除', 'success');

}



async function voiceoverV2DownloadAudio(lineIndex, audioId) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const line = window.voiceoverLinesData[lineIndex];

    if (!line?.tts || !Array.isArray(line.tts.generatedAudios)) return;



    const targetId = String(audioId || '').trim();

    const target = line.tts.generatedAudios.find(item => String(item?.id || '') === targetId);

    const url = String(target?.url || '').trim();

    if (!url) {

        showToast('音频地址不存在，无法下载', 'warning');

        return;

    }



    const baseNameRaw = String(target?.name || `配音_${line?.lineId || lineIndex}`).trim() || '配音';

    const baseName = baseNameRaw.replace(/[\\/:*?"<>|]+/g, '_');

    const extFromUrl = (() => {

        try {

            const parsed = new URL(url, window.location.origin);

            const m = (parsed.pathname || '').match(/\.([a-zA-Z0-9]{2,6})$/);

            return m ? `.${m[1].toLowerCase()}` : '';

        } catch (error) {

            return '';

        }

    })();



    try {

        let response;

        if (/^https?:\/\//i.test(url)) {

            response = await fetch(url);

        } else {

            response = await apiRequest(url, { method: 'GET' });

        }

        if (!response?.ok) {

            throw new Error(`HTTP ${response?.status || 0}`);

        }

        const blob = await response.blob();

        if (!blob || Number(blob.size || 0) <= 0) {

            throw new Error('音频数据为空');

        }



        let extension = extFromUrl;

        if (!extension) {

            const mime = String(blob.type || '').toLowerCase();

            if (mime.includes('wav')) extension = '.wav';

            else if (mime.includes('mpeg') || mime.includes('mp3')) extension = '.mp3';

            else if (mime.includes('ogg')) extension = '.ogg';

            else extension = '.mp3';

        }



        const blobUrl = URL.createObjectURL(blob);

        const a = document.createElement('a');

        a.href = blobUrl;

        a.download = `${baseName}${extension}`;

        document.body.appendChild(a);

        a.click();

        a.remove();

        setTimeout(() => URL.revokeObjectURL(blobUrl), 1500);

    } catch (error) {

        showToast('下载失败: ' + error.message, 'error');

    }

}



function voiceoverV2BuildGeneratedAudios(line, lineIndex) {

    const audiosRaw = Array.isArray(line.tts?.generatedAudios) ? line.tts.generatedAudios : [];

    const audios = audiosRaw

        .filter(item => String(item?.status || 'completed').toLowerCase() !== 'deleted')

        .map((item, idx) => ({ item, idx }))

        .sort((a, b) => {

            const ta = Date.parse(a.item?.createdAt || a.item?.created_at || '');

            const tb = Date.parse(b.item?.createdAt || b.item?.created_at || '');

            const aValid = Number.isFinite(ta);

            const bValid = Number.isFinite(tb);

            if (aValid && bValid && ta !== tb) return tb - ta;

            if (aValid !== bValid) return aValid ? -1 : 1;

            return b.idx - a.idx;

        })

        .map(entry => entry.item);

    if (audios.length === 0) {

        return '<div class="voiceover-generated-empty">尚未生成音频</div>';

    }

    return audios.map((item, index) => {

        const title = item.name || `生成结果 ${index + 1}`;

        const timeText = item.createdAt || item.created_at || '';

        const audioId = String(item.id || `tts_${lineIndex}_${index}`);

        return `

            <div class="voiceover-generated-item">

                <div class="voiceover-generated-head">

                    <div class="voiceover-generated-meta">${escapeHtml(title)} · ${escapeHtml(timeText)}</div>

                    <div class="voiceover-generated-actions">

                        <button class="secondary-button voiceover-mini-btn" onclick='voiceoverV2DownloadAudio(${lineIndex}, ${JSON.stringify(audioId)})'>下载</button>

                        <button class="secondary-button voiceover-mini-btn danger" onclick='voiceoverV2SoftDeleteAudio(${lineIndex}, ${JSON.stringify(audioId)})'>删除</button>

                    </div>

                </div>

                <div class="voiceover-custom-player">

                    <audio preload="metadata" src="${escapeHtml(item.url || '')}"></audio>

                    <div class="voiceover-player-row">

                        <button class="secondary-button voiceover-player-play" type="button">播放</button>

                        <input class="voiceover-player-slider" type="range" min="0" max="1000" step="1" value="0" />

                        <span class="voiceover-player-time">00:00 / 00:00</span>

                    </div>

                </div>

            </div>

        `;

    }).join('');

}



function voiceoverV2BuildMethodControls(line, lineIndex) {

    const scriptState = voiceoverV2EnsureScriptState();

    const method = line.tts?.emotionControlMethod || '与音色参考音频相同';



    if (method === '使用情感向量控制') {

        return `

            <div class="voiceover-op-item">

                <label>向量参数</label>

                <div class="voiceover-inline-controls">

                    <button class="secondary-button voiceover-mini-btn" onclick="voiceoverV2OpenVectorModal(${lineIndex})">设置向量/预设</button>

                </div>

            </div>

        `;

    }



    if (method === '使用情感参考音频') {

        const options = [

            '<option value="">请选择情感参考音频</option>',

            ...scriptState.emotionAudioPresets.map(item => `

                <option value="${escapeHtml(item.id)}" ${line.tts?.emotionAudioPresetId === item.id ? 'selected' : ''}>

                    ${escapeHtml(item.name)}

                </option>

            `)

        ].join('');

        return `

            <div class="voiceover-op-item">

                <label>情感参考音频</label>

                <div class="voiceover-inline-controls">

                    <select class="form-input" onchange="voiceoverV2OnEmotionAudioPresetChange(${lineIndex}, this.value)">

                        ${options}

                    </select>

                    <button class="secondary-button voiceover-mini-btn" onclick="voiceoverV2OpenEmotionAudioModal(${lineIndex})">上传/管理</button>

                </div>

            </div>

        `;

    }



    if (method === '使用情感描述文本控制') {

        return `

            <div class="voiceover-op-item">

                <label>情感文本</label>

                <div class="voiceover-op-note">使用当前行“情绪”输入框文本作为情感描述</div>

            </div>

        `;

    }



    return `

        <div class="voiceover-op-item">

            <label>情感设置</label>

            <div class="voiceover-op-note">沿用音色参考音频自身情感</div>

        </div>

    `;

}



function voiceoverV2IsInsertedLine(line) {

    const lineId = String(line?.lineId || '').trim();

    return lineId.startsWith('line_');

}



function voiceoverV2BuildShotTitle(line, lineIndex) {

    if (!voiceoverV2IsInsertedLine(line)) {

        return `镜头${String(line?.shotNumber || '')}`;

    }

    const lines = Array.isArray(window.voiceoverLinesData) ? window.voiceoverLinesData : [];

    let insertedOrder = 0;

    for (let i = 0; i <= lineIndex; i += 1) {

        if (voiceoverV2IsInsertedLine(lines[i])) insertedOrder += 1;

    }

    return `新增镜头${insertedOrder || 1}`;

}



function voiceoverV2BuildLineCard(line, lineIndex, characters) {

    const scriptState = voiceoverV2EnsureScriptState();

    const status = String(line.tts?.generateStatus || '').toLowerCase();

    const isGenerating = ['pending', 'processing'].includes(status);

    const generateButtonText = status === 'pending'

        ? '排队中...'

        : (status === 'processing'

            ? '生成中...'

            : (status === 'failed' ? '重试生成' : '生成'));

    const methodOptions = VOICEOVER_V2_METHODS.map(method => `

        <option value="${escapeHtml(method)}" ${line.tts?.emotionControlMethod === method ? 'selected' : ''}>${escapeHtml(method)}</option>

    `).join('');



    const voiceRefOptions = [

        '<option value="">请选择音色参考音频</option>',

        ...scriptState.voiceReferences.map(item => `

            <option value="${escapeHtml(item.id)}" ${line.tts?.voiceReferenceId === item.id ? 'selected' : ''}>

                ${escapeHtml(item.name)}

            </option>

        `)

    ].join('');



    const speakerOptions = [

        `<option value="${escapeHtml(line.speaker || '')}">${escapeHtml(line.speaker || '未命名')}</option>`,

        ...characters

            .filter(item => item.name !== line.speaker)

            .map(item => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`)

    ].join('');



    return `

        <div class="voiceover-line-card" data-line-index="${lineIndex}">

            <div class="voiceover-line-top">

                <div class="voiceover-line-title">#${line.lineNumber} · ${escapeHtml(voiceoverV2BuildShotTitle(line, lineIndex))} · ${line.type === 'narration' ? '旁白' : '对白'}</div>

                <div class="voiceover-line-subtitle">${escapeHtml(String(line.lineId || ''))}</div>

            </div>



            <div class="voiceover-op-row">

                <div class="voiceover-op-item">

                    <label>情感控制方式</label>

                    <select class="form-input" onchange="voiceoverV2OnMethodChange(${lineIndex}, this.value)">

                        ${methodOptions}

                    </select>

                </div>

                <div class="voiceover-op-item">

                    <label>音色参考音频（必选）</label>

                    <div class="voiceover-inline-controls">

                        <select class="form-input" onchange="voiceoverV2OnVoiceRefChange(${lineIndex}, this.value)">

                            ${voiceRefOptions}

                        </select>

                        <button class="secondary-button voiceover-mini-btn" onclick="voiceoverV2OpenVoiceRefManager(${lineIndex})">上传/管理</button>

                    </div>

                </div>

                ${voiceoverV2BuildMethodControls(line, lineIndex)}

            </div>

            ${line.tts?.generateError ? `<div class="voiceover-op-subtext voiceover-op-error">${escapeHtml(line.tts.generateError)}</div>` : ''}



            <div class="voiceover-content-row">

                <div class="voiceover-text-cell voiceover-text-cell-full">

                    <label>对白/旁白</label>

                    <textarea class="form-textarea voiceover-textarea"

                              placeholder="输入对白/旁白内容"

                              onblur="voiceoverV2OnLineFieldBlur(${lineIndex}, 'text', this.value)">${escapeHtml(line.text || '')}</textarea>

                </div>

                <div class="voiceover-meta-grid voiceover-meta-grid-inline">

                    <div class="voiceover-meta-item">

                        <label>说话人</label>

                        <select class="form-input" onchange="voiceoverV2OnLineFieldBlur(${lineIndex}, 'speaker', this.value)">

                            ${speakerOptions}

                        </select>

                    </div>

                    <div class="voiceover-meta-item">

                        <label>性别</label>

                        <select class="form-input" onchange="voiceoverV2OnLineFieldBlur(${lineIndex}, 'gender', this.value)">

                            <option value="男" ${line.gender === '男' ? 'selected' : ''}>男</option>

                            <option value="女" ${line.gender === '女' ? 'selected' : ''}>女</option>

                            <option value="中" ${line.gender === '中' ? 'selected' : ''}>中</option>

                            <option value="未知" ${line.gender === '未知' || !line.gender ? 'selected' : ''}>未知</option>

                        </select>

                    </div>

                    <div class="voiceover-meta-item">

                        <label>情绪</label>

                        <input class="form-input" type="text" placeholder="情绪" value="${escapeHtml(line.emotion || '')}" onblur="voiceoverV2OnLineFieldBlur(${lineIndex}, 'emotion', this.value)" />

                    </div>

                </div>

            </div>

            <div class="voiceover-line-actions">

                <div class="voiceover-shot-actions">

                    <button class="secondary-button voiceover-mini-btn" onclick="voiceoverV2InsertLine(${lineIndex})" ${isGenerating ? 'disabled' : ''}>添加镜头</button>

                    <button class="secondary-button voiceover-mini-btn danger" onclick="voiceoverV2DeleteLine(${lineIndex})">删除镜头</button>

                    <button class="secondary-button voiceover-mini-btn" onclick="voiceoverV2SaveLineTemplate(${lineIndex})">保存参数</button>

                </div>

                <button class="primary-button voiceover-generate-btn voiceover-generate-btn-inline" onclick="voiceoverV2Generate(${lineIndex})" ${isGenerating ? 'disabled' : ''}>${generateButtonText}</button>

            </div>



            <div class="voiceover-generated-list">

                ${voiceoverV2BuildGeneratedAudios(line, lineIndex)}

            </div>

        </div>

    `;

}



function voiceoverV2ReindexLineNumbers() {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    window.voiceoverLinesData.forEach((item, index) => {

        if (!item || typeof item !== 'object') return;

        item.lineNumber = index + 1;

    });

}



function voiceoverV2BuildInsertedShotNumber(lineIndex) {

    if (!Array.isArray(window.voiceoverLinesData)) return 1;

    const lines = window.voiceoverLinesData;

    const used = new Set(lines.map(item => String(item?.shotNumber ?? '').trim()).filter(Boolean));

    const toNumber = value => {

        const n = Number(String(value ?? '').trim());

        return Number.isFinite(n) ? n : null;

    };

    const formatNumber = value => {

        const normalized = Number(Number(value).toFixed(6));

        if (!Number.isFinite(normalized)) return '';

        if (Number.isInteger(normalized)) return String(normalized);

        return String(normalized).replace(/\.?0+$/, '');

    };



    const prev = toNumber(lines[lineIndex]?.shotNumber);

    const next = toNumber(lines[lineIndex + 1]?.shotNumber);

    const numericList = lines

        .map(item => toNumber(item?.shotNumber))

        .filter(item => item !== null);

    const maxShot = numericList.length > 0 ? Math.max(...numericList) : 0;



    let candidate = null;

    if (prev !== null && next !== null && next > prev) {

        candidate = (prev + next) / 2;

    } else if (prev !== null) {

        candidate = prev + 1;

    } else {

        candidate = maxShot + 1;

    }



    let candidateText = formatNumber(candidate) || String(maxShot + 1);

    let guard = 0;

    while (used.has(candidateText) && guard < 1000) {

        candidate += 0.001;

        candidateText = formatNumber(candidate);

        guard += 1;

    }

    if (!candidateText || used.has(candidateText)) {

        candidateText = String(maxShot + 1 + Math.max(guard, 1));

    }

    return candidateText;

}



function voiceoverV2InsertLine(lineIndex) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const baseLine = window.voiceoverLinesData[lineIndex];

    if (!baseLine) return;



    const scriptState = voiceoverV2EnsureScriptState();

    const defaultTts = voiceoverV2DefaultLineTts(scriptState);

    const femaleRefId = voiceoverV2DefaultFemaleVoiceRefId();

    const insertedShotNumber = voiceoverV2BuildInsertedShotNumber(lineIndex);



    const newLine = {

        lineNumber: 0,

        shotNumber: insertedShotNumber,

        text: '',

        speaker: '',

        gender: '未知',

        emotion: '',

        type: 'dialogue',

        target: null,

        lineId: voiceoverV2Id('line'),

        tts: {

            ...defaultTts,

            emotionControlMethod: '与音色参考音频相同',

            voiceReferenceId: femaleRefId,

            vectorPresetId: '',

            emotionAudioPresetId: '',

            vectorConfig: voiceoverV2NormalizeVectorConfig({ neutral: 1, weight: 0.65 }),

            generatedAudios: [],

            generateStatus: 'idle',

            generateError: '',

            latestTaskId: ''

        }

    };



    window.voiceoverLinesData.splice(lineIndex + 1, 0, newLine);

    voiceoverV2ReindexLineNumbers();

    voiceoverV2ScheduleAutoSave();

    voiceoverV2Refresh();

    showToast('已在当前行下方添加空镜头', 'success');

}



function voiceoverV2DeleteLine(lineIndex) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    if (window.voiceoverLinesData.length <= 1) {

        showToast('至少保留一条镜头', 'warning');

        return;

    }

    const line = window.voiceoverLinesData[lineIndex];

    if (!line) return;

    const status = String(line.tts?.generateStatus || '').toLowerCase();

    if (status === 'pending' || status === 'processing') {

        showToast('排队中或生成中的镜头不可删除', 'warning');

        return;

    }



    window.voiceoverLinesData.splice(lineIndex, 1);

    voiceoverV2ReindexLineNumbers();

    voiceoverV2ScheduleAutoSave();

    voiceoverV2Refresh();

    showToast('镜头已删除', 'success');

}



function voiceoverV2BuildTemplateSettingsFromLine(line) {

    return {

        emotion_control_method: String(line?.tts?.emotionControlMethod || '与音色参考音频相同'),

        voice_reference_id: String(line?.tts?.voiceReferenceId || ''),

        vector_preset_id: String(line?.tts?.vectorPresetId || ''),

        emotion_audio_preset_id: String(line?.tts?.emotionAudioPresetId || ''),

        vector_config: voiceoverV2NormalizeVectorConfig(line?.tts?.vectorConfig)

    };

}



function voiceoverV2ApplyTemplateSettingsToLine(line, settings) {

    if (!line || !line.tts) return;

    const source = settings && typeof settings === 'object' ? settings : {};

    const scriptState = voiceoverV2EnsureScriptState();

    const methodRaw = String(source.emotion_control_method || source.emotionControlMethod || '与音色参考音频相同');

    line.tts.emotionControlMethod = VOICEOVER_V2_METHODS.includes(methodRaw) ? methodRaw : '与音色参考音频相同';

    const templateRefId = String(source.voice_reference_id || source.voiceReferenceId || line.tts.voiceReferenceId || '').trim();

    line.tts.voiceReferenceId = templateRefId;

    if (!line.tts.voiceReferenceId || !scriptState.voiceReferences.some(item => item.id === line.tts.voiceReferenceId)) {

        line.tts.voiceReferenceId = voiceoverV2DefaultFemaleVoiceRefId();

    }

    line.tts.vectorPresetId = String(source.vector_preset_id || source.vectorPresetId || '');

    line.tts.emotionAudioPresetId = String(source.emotion_audio_preset_id || source.emotionAudioPresetId || '');

    line.tts.vectorConfig = voiceoverV2NormalizeVectorConfig(source.vector_config || source.vectorConfig);

}



async function voiceoverV2SaveLineTemplate(lineIndex) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const line = window.voiceoverLinesData[lineIndex];

    if (!line?.tts) return;



    const scriptState = voiceoverV2EnsureScriptState();

    const defaultName = `模板${(scriptState.settingTemplates?.length || 0) + 1}`;

    const name = await showInputModal('保存参数模板', '模板名称', defaultName, '请输入模板名称');

    if (name === null) return;

    const templateName = String(name || '').trim();

    if (!templateName) {

        showToast('模板名称不能为空', 'warning');

        return;

    }



    const existed = (scriptState.settingTemplates || []).find(item => item.name === templateName);

    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover/shared/setting-templates`, {

            method: 'POST',

            body: JSON.stringify({

                id: existed?.id || '',

                name: templateName,

                settings: voiceoverV2BuildTemplateSettingsFromLine(line)

            })

        });

        const data = await response.json();

        if (!response.ok) throw new Error(data.detail || '保存失败');

        voiceoverV2ApplySharedState(data.shared || {});

        showToast(existed ? '模板已更新' : '模板已保存', 'success');

    } catch (error) {

        showToast('模板保存失败: ' + error.message, 'error');

    }

}



function voiceoverV2BuildBatchTemplateLineRows() {

    const lines = Array.isArray(window.voiceoverLinesData) ? window.voiceoverLinesData : [];

    return lines.map((line, index) => {

        const title = `${line.lineNumber || index + 1} · ${voiceoverV2BuildShotTitle(line, index)}`;

        const subtitle = `${line.type === 'narration' ? '旁白' : '对白'} · ${line.speaker || '未命名'} · ${line.text || ''}`.trim();

        return `

            <label class="voiceover-batch-line-item">

                <input type="checkbox" class="voiceover-batch-line-check" data-line-index="${index}" checked />

                <div class="voiceover-batch-line-text">

                    <div class="voiceover-batch-line-title">${escapeHtml(title)}</div>

                    <div class="voiceover-batch-line-subtitle">${escapeHtml(subtitle)}</div>

                </div>

            </label>

        `;

    }).join('');

}



function voiceoverV2OpenBatchTemplateModal() {

    const scriptState = voiceoverV2EnsureScriptState();

    const templates = Array.isArray(scriptState.settingTemplates) ? scriptState.settingTemplates : [];

    if (templates.length === 0) {

        showToast('暂无参数模板，请先在某行点击“保存参数”', 'warning');

        return;

    }



    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'voiceoverV2BatchTemplateModal';

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="voiceoverV2CloseBatchTemplateModal()"></div>

        <div class="modal-content voiceover-modal">

            <div class="modal-header">

                <h3>批量设置参数</h3>

                <button class="modal-close" onclick="voiceoverV2CloseBatchTemplateModal()">&times;</button>

            </div>

            <div class="modal-body">

                <div class="voiceover-modal-block">

                    <label>选择模板</label>

                    <div class="voiceover-inline-controls">

                        <select id="voiceoverV2BatchTemplateSelect" class="form-input">

                            ${templates.map((item, index) => `<option value="${escapeHtml(item.id)}" ${index === 0 ? 'selected' : ''}>${escapeHtml(item.name)}</option>`).join('')}

                        </select>

                        <button class="secondary-button voiceover-mini-btn danger" onclick="voiceoverV2DeleteBatchTemplate()">删除模板</button>

                    </div>

                </div>

                <div class="voiceover-modal-block">

                    <div class="voiceover-inline-controls">

                        <button class="secondary-button voiceover-mini-btn" onclick="voiceoverV2BatchToggleAllLines(true)">全选</button>

                        <button class="secondary-button voiceover-mini-btn" onclick="voiceoverV2BatchToggleAllLines(false)">取消全选</button>

                    </div>

                    <div class="voiceover-batch-line-list" id="voiceoverV2BatchLineList">

                        ${voiceoverV2BuildBatchTemplateLineRows()}

                    </div>

                </div>

            </div>

            <div class="modal-footer voiceover-modal-footer">

                <button class="secondary-button" onclick="voiceoverV2CloseBatchTemplateModal()">取消</button>

                <button class="primary-button" onclick="voiceoverV2ApplyBatchTemplate()">确认覆盖</button>

            </div>

        </div>

    `;

    document.body.appendChild(modal);

}



function voiceoverV2CloseBatchTemplateModal() {

    const modal = document.getElementById('voiceoverV2BatchTemplateModal');

    if (modal) modal.remove();

}



function voiceoverV2BatchToggleAllLines(checked) {

    const checkboxes = document.querySelectorAll('#voiceoverV2BatchTemplateModal .voiceover-batch-line-check');

    checkboxes.forEach(item => {

        item.checked = !!checked;

    });

}



async function voiceoverV2DeleteBatchTemplate() {

    const scriptState = voiceoverV2EnsureScriptState();

    const templateId = String(document.getElementById('voiceoverV2BatchTemplateSelect')?.value || '').trim();

    if (!templateId) return;

    const target = (scriptState.settingTemplates || []).find(item => item.id === templateId);

    if (!target) return;



    const confirmed = await showConfirmModal(`确认删除模板「${target.name}」吗？`, '删除模板');

    if (!confirmed) return;

    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover/shared/setting-templates/${encodeURIComponent(templateId)}`, {

            method: 'DELETE'

        });

        const data = await response.json();

        if (!response.ok) throw new Error(data.detail || '删除失败');

        const nextState = voiceoverV2ApplySharedState(data.shared || {});

        const templates = Array.isArray(nextState.settingTemplates) ? nextState.settingTemplates : [];

        if (templates.length === 0) {

            voiceoverV2CloseBatchTemplateModal();

            showToast('模板已删除', 'success');

            return;

        }

        showToast('模板已删除', 'success');

        voiceoverV2CloseBatchTemplateModal();

        voiceoverV2OpenBatchTemplateModal();

    } catch (error) {

        showToast('删除模板失败: ' + error.message, 'error');

    }

}



function voiceoverV2ApplyBatchTemplate() {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const scriptState = voiceoverV2EnsureScriptState();

    const templateId = String(document.getElementById('voiceoverV2BatchTemplateSelect')?.value || '').trim();

    if (!templateId) {

        showToast('请选择模板', 'warning');

        return;

    }

    const template = (scriptState.settingTemplates || []).find(item => item.id === templateId);

    if (!template) {

        showToast('模板不存在', 'warning');

        return;

    }



    const selectedIndexes = Array.from(document.querySelectorAll('#voiceoverV2BatchTemplateModal .voiceover-batch-line-check:checked'))

        .map(item => Number(item.getAttribute('data-line-index')))

        .filter(index => Number.isInteger(index) && index >= 0 && index < window.voiceoverLinesData.length);

    if (selectedIndexes.length === 0) {

        showToast('请至少选择一个镜头', 'warning');

        return;

    }



    selectedIndexes.forEach(index => {

        const line = window.voiceoverLinesData[index];

        if (!line?.tts) return;

        voiceoverV2ApplyTemplateSettingsToLine(line, template.settings);

    });

    voiceoverV2ScheduleAutoSave();

    voiceoverV2CloseBatchTemplateModal();

    voiceoverV2Refresh();

    showToast(`已覆盖 ${selectedIndexes.length} 条镜头参数`, 'success');

}



function voiceoverV2OnLineFieldBlur(lineIndex, field, value) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const line = window.voiceoverLinesData[lineIndex];

    if (!line) return;

    line[field] = typeof value === 'string' ? value : '';

    voiceoverV2ScheduleAutoSave();

}



function voiceoverV2OnMethodChange(lineIndex, value) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const line = window.voiceoverLinesData[lineIndex];

    if (!line || !line.tts) return;

    line.tts.emotionControlMethod = VOICEOVER_V2_METHODS.includes(value) ? value : '与音色参考音频相同';

    if (line.tts.emotionControlMethod !== '使用情感参考音频') {

        line.tts.emotionAudioPresetId = '';

    }

    if (line.tts.emotionControlMethod !== '使用情感向量控制') {

        line.tts.vectorPresetId = '';

    }

    voiceoverV2ScheduleAutoSave();

    voiceoverV2Refresh();

}



function voiceoverV2OnVoiceRefChange(lineIndex, value) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const line = window.voiceoverLinesData[lineIndex];

    if (!line || !line.tts) return;

    line.tts.voiceReferenceId = value || '';

    voiceoverV2ScheduleAutoSave();

}



function voiceoverV2OnEmotionAudioPresetChange(lineIndex, value) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const line = window.voiceoverLinesData[lineIndex];

    if (!line || !line.tts) return;

    line.tts.emotionAudioPresetId = value || '';

    voiceoverV2ScheduleAutoSave();

}



function voiceoverV2OpenVoiceRefManager(targetLineIndex = null) {

    voiceoverV2CloseVoiceRefManager();

    const scriptState = voiceoverV2EnsureScriptState();

    const refItems = Array.isArray(scriptState.voiceReferences) ? scriptState.voiceReferences : [];

    const refListHtml = refItems.length > 0

        ? refItems.map(item => `

                        <div class="voiceover-ref-item">

                            <div class="voiceover-ref-meta">

                                <label class="voiceover-ref-label">音色名称</label>

                                <input

                                    class="form-input voiceover-ref-name-input"

                                    type="text"

                                    maxlength="40"

                                    value="${escapeHtml(item.name || '')}"

                                />

                            </div>

                            <div class="voiceover-ref-actions">

                                <button class="secondary-button voiceover-mini-btn" onclick="voiceoverV2RenameVoiceRef('${escapeHtml(item.id)}', this)">保存名称</button>

                                <button class="secondary-button voiceover-mini-btn voiceover-preview-btn" onclick="voiceoverV2ToggleVoiceRefPreview('${escapeHtml(item.id)}', this)">试听</button>

                                <button class="secondary-button voiceover-mini-btn danger" onclick="voiceoverV2DeleteVoiceRef('${escapeHtml(item.id)}')">删除</button>

                            </div>

                        </div>

                    `).join('')

        : '<div class="voiceover-ref-empty">暂无音色参考音频，先上传一个吧</div>';

    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'voiceoverV2VoiceRefModal';

    modal.dataset.targetLineIndex = Number.isInteger(targetLineIndex) ? String(targetLineIndex) : '';

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="voiceoverV2CloseVoiceRefManager()"></div>

        <div class="modal-content voiceover-modal voiceover-ref-modal">

            <div class="modal-header">

                <h3>音色参考音频管理</h3>

                <button class="modal-close" onclick="voiceoverV2CloseVoiceRefManager()">&times;</button>

            </div>

            <div class="modal-body">

                <div class="voiceover-ref-list-wrap">

                    <div class="voiceover-ref-list">

                        ${refListHtml}

                    </div>

                </div>

            </div>

            <div class="modal-footer voiceover-modal-footer voiceover-ref-modal-footer">

                <button class="secondary-button" onclick="voiceoverV2CloseVoiceRefManager()">关闭</button>

                <button class="primary-button" onclick="voiceoverV2UploadVoiceRef()">上传音色参考音频</button>

            </div>

        </div>

    `;

    document.body.appendChild(modal);

}



function voiceoverV2CloseVoiceRefManager() {

    voiceoverV2StopVoiceRefPreview();

    const modal = document.getElementById('voiceoverV2VoiceRefModal');

    if (modal) modal.remove();

}



function voiceoverV2StopVoiceRefPreview() {

    const audio = APP_STATE.voiceoverRefPreviewAudio;

    if (audio) {

        try {

            audio.pause();

            audio.currentTime = 0;

        } catch (error) {

            console.warn('[voiceoverV2StopVoiceRefPreview] stop failed:', error);

        }

    }

    const blobUrl = String(APP_STATE.voiceoverRefPreviewBlobUrl || '');

    if (blobUrl) {

        try {

            URL.revokeObjectURL(blobUrl);

        } catch (error) {

            console.warn('[voiceoverV2StopVoiceRefPreview] revoke object url failed:', error);

        }

    }

    APP_STATE.voiceoverRefPreviewAudio = null;

    APP_STATE.voiceoverRefPreviewId = '';

    APP_STATE.voiceoverRefPreviewBlobUrl = '';

    document.querySelectorAll('.voiceover-preview-btn.playing').forEach(btn => {

        btn.classList.remove('playing');

        btn.textContent = '试听';

    });

}



async function voiceoverV2ToggleVoiceRefPreview(voiceRefId, buttonEl) {

    const targetId = String(voiceRefId || '').trim();

    if (!targetId || !buttonEl) return;



    const currentAudio = APP_STATE.voiceoverRefPreviewAudio;

    const currentId = String(APP_STATE.voiceoverRefPreviewId || '');

    if (currentAudio && currentId === targetId && !currentAudio.paused) {

        voiceoverV2StopVoiceRefPreview();

        return;

    }



    voiceoverV2StopVoiceRefPreview();



    const scriptState = voiceoverV2EnsureScriptState();

    const target = scriptState.voiceReferences.find(item => item.id === targetId);

    const previewUrlRaw = String(target?.url || '').trim();

    const previewUrl = previewUrlRaw || (

        APP_STATE.currentEpisode

            ? `/api/episodes/${APP_STATE.currentEpisode}/voiceover/shared/voice-references/${encodeURIComponent(targetId)}/preview`

            : ''

    );

    if (!previewUrl) {

        showToast('该音频不可试听', 'warning');

        return;

    }



    try {

        let audioSource = previewUrlRaw;

        if (!audioSource) {

            const response = await apiRequest(previewUrl, { method: 'GET' });

            if (!response?.ok) {

                let message = `HTTP ${response?.status || 0}`;

                try {

                    const data = await response.json();

                    if (data?.detail) message = String(data.detail);

                } catch (error) {

                    // keep fallback HTTP message

                }

                throw new Error(message);

            }

            const audioBlob = await response.blob();

            if (!audioBlob || Number(audioBlob.size || 0) <= 0) {

                throw new Error('音频数据为空');

            }

            audioSource = URL.createObjectURL(audioBlob);

            APP_STATE.voiceoverRefPreviewBlobUrl = audioSource;

        }



        const audio = new Audio(audioSource);

        APP_STATE.voiceoverRefPreviewAudio = audio;

        APP_STATE.voiceoverRefPreviewId = targetId;

        buttonEl.classList.add('playing');

        buttonEl.textContent = '停止';



        const reset = () => {

            if (APP_STATE.voiceoverRefPreviewAudio === audio) {

                APP_STATE.voiceoverRefPreviewAudio = null;

                APP_STATE.voiceoverRefPreviewId = '';

            }

            buttonEl.classList.remove('playing');

            buttonEl.textContent = '试听';

        };



        audio.addEventListener('ended', reset, { once: true });

        audio.addEventListener('error', () => {

            reset();

            showToast('试听失败', 'error');

        }, { once: true });

        await audio.play();

    } catch (error) {

        voiceoverV2StopVoiceRefPreview();

        showToast('试听失败: ' + error.message, 'error');

    }

}



function voiceoverV2UseVoiceRefForLine(lineIndex, voiceRefId) {

    voiceoverV2OnVoiceRefChange(lineIndex, voiceRefId);

    voiceoverV2Refresh();

    voiceoverV2CloseVoiceRefManager();

}



async function voiceoverV2UploadVoiceRef() {

    const modal = document.getElementById('voiceoverV2VoiceRefModal');

    const targetRaw = modal?.dataset?.targetLineIndex;

    const targetLineIndex = targetRaw === '' ? null : Number(targetRaw);



    const input = document.createElement('input');

    input.type = 'file';

    input.accept = 'audio/*';

    input.onchange = async (event) => {

        const file = event.target.files && event.target.files[0];

        if (!file) return;

        const autoNameRaw = file.name.replace(/\.[^.]+$/, '').trim();

        const autoName = autoNameRaw || '新音色';



        try {

            const form = new FormData();

            form.append('name', autoName);

            form.append('file', file);

            const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover/shared/voice-references`, {

                method: 'POST',

                body: form

            });

            const data = await response.json();

            if (!response.ok) throw new Error(data.detail || '上传失败');

            voiceoverV2ApplySharedState(data.shared || {});



            if (Number.isInteger(targetLineIndex) && Array.isArray(window.voiceoverLinesData)) {

                const line = window.voiceoverLinesData[targetLineIndex];

                if (line?.tts) line.tts.voiceReferenceId = String(data.item?.id || '');

                voiceoverV2ScheduleAutoSave();

            }

            showToast('音色参考音频上传成功', 'success');

            voiceoverV2OpenVoiceRefManager(Number.isInteger(targetLineIndex) ? targetLineIndex : null);

            voiceoverV2Refresh();

        } catch (error) {

            showToast('上传失败: ' + error.message, 'error');

        }

    };

    input.click();

}



async function voiceoverV2RenameVoiceRef(voiceRefId, buttonEl) {

    const targetId = String(voiceRefId || '').trim();

    if (!targetId) return;



    const scriptState = voiceoverV2EnsureScriptState();

    const target = scriptState.voiceReferences.find(item => item.id === targetId);

    if (!target) return;



    const card = buttonEl?.closest('.voiceover-ref-item');

    const input = card?.querySelector('.voiceover-ref-name-input');

    const nextName = String(input?.value || '').trim();

    if (!nextName) {

        showToast('音色名称不能为空', 'warning');

        return;

    }

    if (nextName === String(target.name || '').trim()) {

        showToast('名称未变化', 'warning');

        return;

    }



    if (buttonEl) {

        buttonEl.disabled = true;

        buttonEl.textContent = '保存中...';

    }



    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover/shared/voice-references/${encodeURIComponent(targetId)}`, {

            method: 'PUT',

            body: JSON.stringify({ name: nextName })

        });

        const data = await response.json();

        if (!response.ok) throw new Error(data.detail || '保存失败');



        voiceoverV2ApplySharedState(data.shared || {});

        showToast('音色名称已更新', 'success');



        const modal = document.getElementById('voiceoverV2VoiceRefModal');

        const targetRaw = modal?.dataset?.targetLineIndex;

        const targetLineIndex = targetRaw === '' ? null : Number(targetRaw);

        voiceoverV2OpenVoiceRefManager(Number.isInteger(targetLineIndex) ? targetLineIndex : null);

        voiceoverV2Refresh();

    } catch (error) {

        showToast('修改失败: ' + error.message, 'error');

        if (input) input.value = String(target.name || '');

    } finally {

        if (buttonEl) {

            buttonEl.disabled = false;

            buttonEl.textContent = '保存名称';

        }

    }

}



async function voiceoverV2DeleteVoiceRef(voiceRefId) {

    const scriptState = voiceoverV2EnsureScriptState();

    const target = scriptState.voiceReferences.find(item => item.id === voiceRefId);

    if (!target) return;



    const confirmed = await showConfirmModal(`确认删除音色参考音频「${target.name}」吗？`, '删除音色参考音频');

    if (!confirmed) return;



    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover/shared/voice-references/${encodeURIComponent(voiceRefId)}`, {

            method: 'DELETE'

        });

        const data = await response.json();

        if (!response.ok) throw new Error(data.detail || '删除失败');

        voiceoverV2ApplySharedState(data.shared || {});



        const fallbackId = String(data.fallback_voice_reference_id || '');

        if (Array.isArray(window.voiceoverLinesData)) {

            window.voiceoverLinesData.forEach(line => {

                if (line?.tts?.voiceReferenceId === voiceRefId) {

                    line.tts.voiceReferenceId = fallbackId;

                }

            });

            voiceoverV2ScheduleAutoSave();

        }



        const modal = document.getElementById('voiceoverV2VoiceRefModal');

        const targetRaw = modal?.dataset?.targetLineIndex;

        const targetLineIndex = targetRaw === '' ? null : Number(targetRaw);

        voiceoverV2OpenVoiceRefManager(Number.isInteger(targetLineIndex) ? targetLineIndex : null);

        voiceoverV2Refresh();

    } catch (error) {

        showToast('删除失败: ' + error.message, 'error');

    }

}



function voiceoverV2OpenVectorModal(lineIndex) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const line = window.voiceoverLinesData[lineIndex];

    if (!line?.tts) return;

    const vector = voiceoverV2NormalizeVectorConfig(line.tts.vectorConfig);

    const scriptState = voiceoverV2EnsureScriptState();

    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'voiceoverV2VectorModal';

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="voiceoverV2CloseVectorModal()"></div>

        <div class="modal-content voiceover-modal">

            <div class="modal-header">

                <h3>设置情感向量（8维）</h3>

                <button class="modal-close" onclick="voiceoverV2CloseVectorModal()">&times;</button>

            </div>

            <div class="modal-body">

                <div class="voiceover-modal-block">

                    <label>预设</label>

                    <div class="voiceover-inline-controls">

                        <select class="form-input" id="voiceoverV2VectorPresetSelect" onchange="voiceoverV2ApplyVectorPreset(true)">

                            <option value="">请选择预设</option>

                            ${scriptState.vectorPresets.map(item => `

                                <option value="${escapeHtml(item.id)}" ${line.tts.vectorPresetId === item.id ? 'selected' : ''}>${escapeHtml(item.name)}</option>

                            `).join('')}

                        </select>

                        <button class="secondary-button voiceover-mini-btn danger" onclick="voiceoverV2DeleteVectorPreset(${lineIndex})">删除预设</button>

                    </div>

                </div>

                <div class="voiceover-vector-grid">

                    <div class="voiceover-vector-item">

                        <label>权重</label>

                        <input class="form-input" type="number" min="0" max="1" step="0.01" id="voiceoverV2VecWeight" value="${vector.weight}">

                    </div>

                    ${VOICEOVER_V2_VECTOR_DIMS.map(item => `

                        <div class="voiceover-vector-item">

                            <label>${item.label}</label>

                            <input class="form-input" type="number" min="0" max="1" step="0.01" id="voiceoverV2Vec_${item.key}" value="${vector[item.key]}">

                        </div>

                    `).join('')}

                </div>

                <div class="voiceover-modal-block">

                    <label>保存为预设</label>

                    <div class="voiceover-inline-controls">

                        <input class="form-input" id="voiceoverV2VectorPresetName" placeholder="预设名称（必填）" />

                        <input class="form-input" id="voiceoverV2VectorPresetDesc" placeholder="描述（选填）" />

                    </div>

                </div>

            </div>

            <div class="modal-footer voiceover-modal-footer">

                <button class="secondary-button" onclick="voiceoverV2CloseVectorModal()">取消</button>

                <button class="secondary-button" onclick="voiceoverV2SaveVectorPreset(${lineIndex})">保存预设</button>

                <button class="primary-button" onclick="voiceoverV2ApplyVectorModal(${lineIndex})">确定</button>

            </div>

        </div>

    `;

    document.body.appendChild(modal);

    const presetSelect = document.getElementById('voiceoverV2VectorPresetSelect');

    if (presetSelect && presetSelect.value) {

        voiceoverV2ApplyVectorPreset(true);

    }

}



function voiceoverV2CloseVectorModal() {

    const modal = document.getElementById('voiceoverV2VectorModal');

    if (modal) modal.remove();

}



function voiceoverV2ReadVectorFromModal() {

    const vector = { weight: Number(document.getElementById('voiceoverV2VecWeight')?.value || 0.65) };

    VOICEOVER_V2_VECTOR_DIMS.forEach(item => {

        vector[item.key] = Number(document.getElementById(`voiceoverV2Vec_${item.key}`)?.value || 0);

    });

    return voiceoverV2NormalizeVectorConfig(vector);

}



function voiceoverV2ApplyVectorPreset(silent = false) {

    const presetId = document.getElementById('voiceoverV2VectorPresetSelect')?.value || '';

    if (!presetId) {

        if (!silent) {

            showToast('请选择预设', 'warning');

        }

        return;

    }

    const scriptState = voiceoverV2EnsureScriptState();

    const preset = scriptState.vectorPresets.find(item => item.id === presetId);

    if (!preset) return;

    const vector = voiceoverV2NormalizeVectorConfig(preset.vectorConfig);

    const weightInput = document.getElementById('voiceoverV2VecWeight');

    if (weightInput) weightInput.value = vector.weight;

    VOICEOVER_V2_VECTOR_DIMS.forEach(item => {

        const input = document.getElementById(`voiceoverV2Vec_${item.key}`);

        if (input) input.value = vector[item.key];

    });

}



function voiceoverV2SaveVectorPreset(lineIndex) {

    const name = (document.getElementById('voiceoverV2VectorPresetName')?.value || '').trim();

    const description = (document.getElementById('voiceoverV2VectorPresetDesc')?.value || '').trim();

    if (!name) {

        showToast('请填写预设名称', 'warning');

        return;

    }

    (async () => {

        try {

            const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover/shared/vector-presets`, {

                method: 'POST',

                body: JSON.stringify({

                    name,

                    description,

                    vector_config: voiceoverV2ReadVectorFromModal()

                })

            });

            const data = await response.json();

            if (!response.ok) throw new Error(data.detail || '保存失败');

            voiceoverV2ApplySharedState(data.shared || {});



            if (Array.isArray(window.voiceoverLinesData)) {

                const line = window.voiceoverLinesData[lineIndex];

                if (line?.tts) line.tts.vectorPresetId = String(data.preset_id || '');

            }

            voiceoverV2ScheduleAutoSave();

            showToast('向量预设已保存', 'success');

            voiceoverV2CloseVectorModal();

            voiceoverV2Refresh();

        } catch (error) {

            showToast('向量预设保存失败: ' + error.message, 'error');

        }

    })();

}



async function voiceoverV2DeleteVectorPreset(lineIndex) {

    const presetId = document.getElementById('voiceoverV2VectorPresetSelect')?.value || '';

    if (!presetId) {

        showToast('请选择预设', 'warning');

        return;

    }

    const scriptState = voiceoverV2EnsureScriptState();

    const preset = scriptState.vectorPresets.find(item => item.id === presetId);

    if (!preset) return;



    const confirmed = await showConfirmModal(`确认删除向量预设「${preset.name}」吗？`, '删除预设');

    if (!confirmed) return;



    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover/shared/vector-presets/${encodeURIComponent(presetId)}`, {

            method: 'DELETE'

        });

        const data = await response.json();

        if (!response.ok) throw new Error(data.detail || '删除失败');

        voiceoverV2ApplySharedState(data.shared || {});



        if (Array.isArray(window.voiceoverLinesData)) {

            window.voiceoverLinesData.forEach(item => {

                if (item?.tts?.vectorPresetId === presetId) item.tts.vectorPresetId = '';

            });

            voiceoverV2ScheduleAutoSave();

        }

        voiceoverV2CloseVectorModal();

        voiceoverV2Refresh();

        if (Number.isInteger(lineIndex)) voiceoverV2OpenVectorModal(lineIndex);

    } catch (error) {

        showToast('删除预设失败: ' + error.message, 'error');

    }

}



function voiceoverV2ApplyVectorModal(lineIndex) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const line = window.voiceoverLinesData[lineIndex];

    if (!line?.tts) return;

    const presetId = document.getElementById('voiceoverV2VectorPresetSelect')?.value || '';

    line.tts.vectorPresetId = presetId;

    line.tts.vectorConfig = voiceoverV2ReadVectorFromModal();

    voiceoverV2ScheduleAutoSave();

    voiceoverV2CloseVectorModal();

    voiceoverV2Refresh();

}



function voiceoverV2OpenEmotionAudioModal(lineIndex) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const line = window.voiceoverLinesData[lineIndex];

    if (!line?.tts) return;

    const scriptState = voiceoverV2EnsureScriptState();

    const modal = document.createElement('div');

    modal.className = 'modal active';

    modal.id = 'voiceoverV2EmotionAudioModal';

    modal.dataset.lineIndex = String(lineIndex);

    modal.innerHTML = `

        <div class="modal-backdrop" onclick="voiceoverV2CloseEmotionAudioModal()"></div>

        <div class="modal-content voiceover-modal">

            <div class="modal-header">

                <h3>情感参考音频预设</h3>

                <button class="modal-close" onclick="voiceoverV2CloseEmotionAudioModal()">&times;</button>

            </div>

            <div class="modal-body">

                <div class="voiceover-modal-block">

                    <label>已有预设</label>

                    <select class="form-input" id="voiceoverV2EmotionAudioPresetSelect">

                        <option value="">请选择预设</option>

                        ${scriptState.emotionAudioPresets.map(item => `

                            <option value="${escapeHtml(item.id)}" ${line.tts.emotionAudioPresetId === item.id ? 'selected' : ''}>${escapeHtml(item.name)}</option>

                        `).join('')}

                    </select>

                </div>

                <div class="voiceover-modal-block">

                    <label>上传新预设</label>

                    <div class="voiceover-inline-controls">

                        <button class="secondary-button voiceover-mini-btn" onclick="voiceoverV2UploadEmotionAudioFile()">选择音频</button>

                        <span class="voiceover-upload-name" id="voiceoverV2EmotionAudioFileName">未选择文件</span>

                    </div>

                    <input class="form-input" id="voiceoverV2EmotionAudioName" placeholder="预设名称（必填）" />

                    <input class="form-input" id="voiceoverV2EmotionAudioDesc" placeholder="描述（选填）" />

                </div>

            </div>

            <div class="modal-footer voiceover-modal-footer">

                <button class="secondary-button" onclick="voiceoverV2DeleteEmotionAudioPreset()">删除预设</button>

                <button class="secondary-button" onclick="voiceoverV2SaveEmotionAudioPreset()">保存预设</button>

                <button class="primary-button" onclick="voiceoverV2ApplyEmotionAudioPreset()">应用</button>

            </div>

        </div>

    `;

    document.body.appendChild(modal);

}



function voiceoverV2CloseEmotionAudioModal() {

    const modal = document.getElementById('voiceoverV2EmotionAudioModal');

    if (modal) modal.remove();

}



function voiceoverV2UploadEmotionAudioFile() {

    const modal = document.getElementById('voiceoverV2EmotionAudioModal');

    if (!modal) return;

    const input = document.createElement('input');

    input.type = 'file';

    input.accept = 'audio/*';

    input.onchange = (event) => {

        const file = event.target.files && event.target.files[0];

        if (!file) return;

        modal._uploadedEmotionAudioFile = file;

        const label = document.getElementById('voiceoverV2EmotionAudioFileName');

        if (label) label.textContent = file.name;

        const nameInput = document.getElementById('voiceoverV2EmotionAudioName');

        if (nameInput && !nameInput.value.trim()) {

            nameInput.value = file.name.replace(/\.[^.]+$/, '');

        }

    };

    input.click();

}



function voiceoverV2SaveEmotionAudioPreset() {

    const modal = document.getElementById('voiceoverV2EmotionAudioModal');

    if (!modal) return;

    const file = modal._uploadedEmotionAudioFile;

    const name = (document.getElementById('voiceoverV2EmotionAudioName')?.value || '').trim();

    const description = (document.getElementById('voiceoverV2EmotionAudioDesc')?.value || '').trim();

    if (!file) {

        showToast('请先选择情感参考音频', 'warning');

        return;

    }

    if (!name) {

        showToast('请填写预设名称', 'warning');

        return;

    }

    (async () => {

        try {

            const form = new FormData();

            form.append('name', name);

            form.append('description', description);

            form.append('file', file);

            const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover/shared/emotion-audio-presets`, {

                method: 'POST',

                body: form

            });

            const data = await response.json();

            if (!response.ok) throw new Error(data.detail || '保存失败');

            voiceoverV2ApplySharedState(data.shared || {});



            const lineIndex = Number(modal.dataset.lineIndex);

            if (Array.isArray(window.voiceoverLinesData) && Number.isInteger(lineIndex)) {

                const line = window.voiceoverLinesData[lineIndex];

                if (line?.tts) line.tts.emotionAudioPresetId = String(data.item?.id || '');

            }



            voiceoverV2ScheduleAutoSave();

            showToast('情感参考音频预设已保存', 'success');

            voiceoverV2CloseEmotionAudioModal();

            voiceoverV2Refresh();

        } catch (error) {

            showToast('保存失败: ' + error.message, 'error');

        }

    })();

}



async function voiceoverV2DeleteEmotionAudioPreset() {

    const select = document.getElementById('voiceoverV2EmotionAudioPresetSelect');

    const presetId = select ? select.value : '';

    if (!presetId) {

        showToast('请选择预设', 'warning');

        return;

    }

    const scriptState = voiceoverV2EnsureScriptState();

    const preset = scriptState.emotionAudioPresets.find(item => item.id === presetId);

    if (!preset) return;

    const confirmed = await showConfirmModal(`确认删除情感预设「${preset.name}」吗？`, '删除预设');

    if (!confirmed) return;

    try {

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover/shared/emotion-audio-presets/${encodeURIComponent(presetId)}`, {

            method: 'DELETE'

        });

        const data = await response.json();

        if (!response.ok) throw new Error(data.detail || '删除失败');

        voiceoverV2ApplySharedState(data.shared || {});



        if (Array.isArray(window.voiceoverLinesData)) {

            window.voiceoverLinesData.forEach(line => {

                if (line?.tts?.emotionAudioPresetId === presetId) {

                    line.tts.emotionAudioPresetId = '';

                }

            });

            voiceoverV2ScheduleAutoSave();

        }

        voiceoverV2CloseEmotionAudioModal();

        voiceoverV2Refresh();

    } catch (error) {

        showToast('删除失败: ' + error.message, 'error');

    }

}



function voiceoverV2ApplyEmotionAudioPreset() {

    const modal = document.getElementById('voiceoverV2EmotionAudioModal');

    if (!modal) return;

    const lineIndex = Number(modal.dataset.lineIndex);

    const presetId = document.getElementById('voiceoverV2EmotionAudioPresetSelect')?.value || '';

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const line = window.voiceoverLinesData[lineIndex];

    if (!line?.tts) return;

    line.tts.emotionAudioPresetId = presetId;

    voiceoverV2ScheduleAutoSave();

    voiceoverV2CloseEmotionAudioModal();

    voiceoverV2Refresh();

}



async function voiceoverV2GenerateAll() {

    if (!Array.isArray(window.voiceoverLinesData) || window.voiceoverLinesData.length === 0) {

        showToast('没有可生成的配音行', 'warning');

        return;

    }



    try {

        await saveVoiceoverTable({ silent: true, auto: true });

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover/generate-all`, {

            method: 'POST',

            body: JSON.stringify({})

        });

        const data = await response.json();

        if (!response.ok) throw new Error(data.detail || '提交失败');



        const enqueuedSet = new Set((data.enqueued_line_ids || []).map(item => String(item || '')));

        const skippedMap = new Map((Array.isArray(data.skipped) ? data.skipped : []).map(item => [

            String(item?.line_id || ''),

            String(item?.reason || '')

        ]));



        window.voiceoverLinesData.forEach(line => {

            const lineId = String(line?.lineId || '');

            if (!line?.tts || !lineId) return;

            if (enqueuedSet.has(lineId)) {

                line.tts.generateStatus = 'pending';

                line.tts.generateError = '';

            } else if (skippedMap.has(lineId)) {

                line.tts.generateError = skippedMap.get(lineId);

            }

        });



        voiceoverV2Refresh();

        voiceoverV2UpdateStatusPolling();



        const enqueuedCount = Number(data.enqueued_count || 0);

        const skippedCount = Number(data.skipped_count || 0);

        if (enqueuedCount > 0) {

            showToast(`已入队 ${enqueuedCount} 条${skippedCount > 0 ? `，跳过 ${skippedCount} 条` : ''}`, 'success');

        } else {

            showToast(`没有可入队的配音行${skippedCount > 0 ? `（跳过 ${skippedCount} 条）` : ''}`, 'warning');

        }

    } catch (error) {

        showToast('提交失败: ' + error.message, 'error');

    }

}



async function voiceoverV2Generate(lineIndex) {

    if (!Array.isArray(window.voiceoverLinesData)) return;

    const line = window.voiceoverLinesData[lineIndex];

    if (!line?.tts) return;

    if (!line.lineId) {

        showToast('line_id 缺失，无法生成', 'error');

        return;

    }

    if (!line.tts.voiceReferenceId) {

        showToast('请先选择音色参考音频', 'warning');

        return;

    }

    if (line.tts.emotionControlMethod === '使用情感参考音频' && !line.tts.emotionAudioPresetId) {

        showToast('请先选择或上传情感参考音频预设', 'warning');

        return;

    }



    try {

        await saveVoiceoverTable({ silent: true, auto: true });

        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover/lines/${encodeURIComponent(line.lineId)}/generate`, {

            method: 'POST',

            body: JSON.stringify({

                text: line.text || '',

                emo_text: line.emotion || '',

                emotion_control_method: line.tts.emotionControlMethod,

                voice_reference_id: line.tts.voiceReferenceId,

                vector_preset_id: line.tts.vectorPresetId || '',

                emotion_audio_preset_id: line.tts.emotionAudioPresetId || '',

                vector_config: voiceoverV2NormalizeVectorConfig(line.tts.vectorConfig)

            })

        });

        const data = await response.json();

        if (!response.ok) throw new Error(data.detail || '提交失败');



        line.tts.generateStatus = 'pending';

        line.tts.generateError = '';

        line.tts.latestTaskId = String(data.task_id || '');

        voiceoverV2Refresh();

        voiceoverV2UpdateStatusPolling();

        showToast('任务已加入队列', 'success');

    } catch (error) {

        showToast('提交失败: ' + error.message, 'error');

    }

}



async function saveVoiceoverTable(options = {}) {

    if (!Array.isArray(window.voiceoverLinesData) || window.voiceoverLinesData.length === 0) {

        if (!options.silent) showToast('没有可保存的数据', 'warning');

        return;

    }



    try {

        APP_STATE.voiceoverSaveState = 'saving';

        voiceoverV2UpdateSaveHint();



        const shotsMap = new Map();

        window.voiceoverLinesData.forEach(line => {

            const shotKey = String(line.shotNumber ?? '');

            if (!shotsMap.has(shotKey)) {

                shotsMap.set(shotKey, {

                    shot_number: line.shotNumber,

                    voice_type: 'none',

                    narration: null,

                    dialogue: []

                });

            }

            const shot = shotsMap.get(shotKey);

            const ttsPayload = {

                emotion_control_method: line.tts?.emotionControlMethod || '与音色参考音频相同',

                voice_reference_id: line.tts?.voiceReferenceId || '',

                vector_preset_id: line.tts?.vectorPresetId || '',

                emotion_audio_preset_id: line.tts?.emotionAudioPresetId || '',

                vector_config: voiceoverV2NormalizeVectorConfig(line.tts?.vectorConfig),

                generated_audios: Array.isArray(line.tts?.generatedAudios) ? line.tts.generatedAudios.map(item => ({

                    id: item.id,

                    name: item.name,

                    url: item.url,

                    task_id: item.taskId || '',

                    created_at: item.createdAt || '',

                    status: item.status || 'completed'

                })) : [],

                generate_status: line.tts?.generateStatus || 'idle',

                generate_error: line.tts?.generateError || '',

                latest_task_id: line.tts?.latestTaskId || ''

            };



            if (line.type === 'narration') {

                shot.voice_type = 'narration';

                shot.narration = {

                    line_id: line.lineId || '',

                    speaker: line.speaker || '',

                    gender: line.gender || '未知',

                    emotion: line.emotion || '',

                    text: line.text || '',

                    tts: ttsPayload

                };

            } else if (line.type === 'dialogue') {

                if (shot.voice_type !== 'narration') shot.voice_type = 'dialogue';

                shot.dialogue.push({

                    line_id: line.lineId || '',

                    speaker: line.speaker || '',

                    gender: line.gender || '未知',

                    emotion: line.emotion || '',

                    text: line.text || '',

                    target: line.target || null,

                    tts: ttsPayload

                });

            }

        });



        const shots = Array.from(shotsMap.values());

        shots.forEach(shot => {

            if (shot.voice_type === 'narration') {

                shot.dialogue = null;

            } else if (shot.voice_type === 'dialogue') {

                shot.narration = null;

                if (!shot.dialogue.length) {

                    shot.voice_type = 'none';

                    shot.dialogue = null;

                }

            } else {

                shot.narration = null;

                shot.dialogue = null;

            }

        });



        const response = await apiRequest(`/api/episodes/${APP_STATE.currentEpisode}/voiceover`, {

            method: 'PUT',

            body: JSON.stringify({ shots })

        });

        if (!response.ok) {

            const errorData = await response.json();

            throw new Error(errorData.detail || '保存失败');

        }



        APP_STATE.voiceoverSaveState = 'saved';

        APP_STATE.voiceoverLastSavedAt = new Date().toLocaleTimeString();

        voiceoverV2UpdateSaveHint();

        voiceoverV2UpdateStatusPolling();

        if (!options.silent) showToast('配音表保存成功', 'success');

    } catch (error) {

        console.error('[saveVoiceoverTable] failed:', error);

        APP_STATE.voiceoverSaveState = 'error';

        voiceoverV2UpdateSaveHint();

        if (!options.silent) showToast('保存配音表失败: ' + error.message, 'error');

    }

}



// 启动应用

document.addEventListener('DOMContentLoaded', initApp);




// 爆款库步骤
async function loadHitDramasStep() {
    const container = document.getElementById('creationContainer');
    container.innerHTML = '<div id="hitDramasContainer"></div>';
    
    // 调用爆款库加载函数
    await loadHitDramas();
}
