const HIT_DRAMA_SORT_STORAGE_KEY = 'hit_drama_sort_mode_v1';
const HIT_DRAMA_SORT_DIRECTION_STORAGE_KEY = 'hit_drama_sort_direction_v1';
const HIT_DRAMA_SORT_BY_ONLINE_TIME = 'online_time';
const HIT_DRAMA_SORT_BY_IMPORT_TIME = 'import_time';
const HIT_DRAMA_SORT_ASC = 'asc';
const HIT_DRAMA_SORT_DESC = 'desc';
const HIT_DRAMA_STATE = {
    dramas: [],
    dramasById: new Map(),
    nextTempId: -1,
    rowHeights: new Map(),
    activeResize: null,
    sortMode: HIT_DRAMA_SORT_BY_ONLINE_TIME,
    sortDirection: HIT_DRAMA_SORT_DESC,
    standaloneView: false
};

const HIT_DRAMA_DEFAULT_ROW_HEIGHT = 194;
const HIT_DRAMA_MIN_ROW_HEIGHT = 194;
const HIT_DRAMA_PREVIEW_BREAK_RATIO = 2 / 3;
let hitDramaMeasureCanvasContext = null;

function normalizeHitDramaValue(value) {
    if (value === null || value === undefined) {
        return '';
    }
    return String(value);
}

function normalizeHitDramaOnlineTimeInput(value) {
    const rawValue = normalizeHitDramaValue(value).trim();
    if (!rawValue) {
        return '';
    }

    const match = rawValue.match(/^(\d{4})[./-](\d{1,2})[./-](\d{1,2})$/);
    if (!match) {
        throw new Error('上线时间格式应为 YYYY.MM.DD');
    }

    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const date = new Date(year, month - 1, day);
    if (
        Number.isNaN(date.getTime()) ||
        date.getFullYear() !== year ||
        date.getMonth() !== month - 1 ||
        date.getDate() !== day
    ) {
        throw new Error('上线时间不是有效日期');
    }

    return `${String(year).padStart(4, '0')}.${String(month).padStart(2, '0')}.${String(day).padStart(2, '0')}`;
}

function getStoredHitDramaSortMode() {
    const storedMode = localStorage.getItem(HIT_DRAMA_SORT_STORAGE_KEY);
    return storedMode === HIT_DRAMA_SORT_BY_IMPORT_TIME
        ? HIT_DRAMA_SORT_BY_IMPORT_TIME
        : HIT_DRAMA_SORT_BY_ONLINE_TIME;
}

function getStoredHitDramaSortDirection() {
    return localStorage.getItem(HIT_DRAMA_SORT_DIRECTION_STORAGE_KEY) === HIT_DRAMA_SORT_ASC
        ? HIT_DRAMA_SORT_ASC
        : HIT_DRAMA_SORT_DESC;
}

function syncHitDramaSortSettings() {
    HIT_DRAMA_STATE.sortMode = getStoredHitDramaSortMode();
    HIT_DRAMA_STATE.sortDirection = getStoredHitDramaSortDirection();
}

function setHitDramaSortMode(nextMode) {
    HIT_DRAMA_STATE.sortMode = nextMode === HIT_DRAMA_SORT_BY_IMPORT_TIME
        ? HIT_DRAMA_SORT_BY_IMPORT_TIME
        : HIT_DRAMA_SORT_BY_ONLINE_TIME;
    localStorage.setItem(HIT_DRAMA_SORT_STORAGE_KEY, HIT_DRAMA_STATE.sortMode);
}

function setHitDramaSortDirection(nextDirection) {
    HIT_DRAMA_STATE.sortDirection = nextDirection === HIT_DRAMA_SORT_ASC
        ? HIT_DRAMA_SORT_ASC
        : HIT_DRAMA_SORT_DESC;
    localStorage.setItem(HIT_DRAMA_SORT_DIRECTION_STORAGE_KEY, HIT_DRAMA_STATE.sortDirection);
}

function getHitDramaSortDirectionLabel() {
    const isAscending = HIT_DRAMA_STATE.sortDirection === HIT_DRAMA_SORT_ASC;
    if (HIT_DRAMA_STATE.sortMode === HIT_DRAMA_SORT_BY_IMPORT_TIME) {
        return isAscending ? '\u65e7\u5230\u65b0' : '\u65b0\u5230\u65e7';
    }
    return isAscending ? '\u65e9\u5230\u665a' : '\u665a\u5230\u65e9';
}

function getHitDramaSortableOnlineTime(value) {
    try {
        return normalizeHitDramaOnlineTimeInput(value);
    } catch (error) {
        return '';
    }
}

function getHitDramaCreatedAtTimestamp(drama) {
    const timestamp = Date.parse(drama?.created_at || '');
    return Number.isFinite(timestamp) ? timestamp : 0;
}

function getSortedHitDramaList(dramas = HIT_DRAMA_STATE.dramas) {
    const sortMode = HIT_DRAMA_STATE.sortMode || HIT_DRAMA_SORT_BY_ONLINE_TIME;
    const sortDirection = HIT_DRAMA_STATE.sortDirection === HIT_DRAMA_SORT_ASC ? 1 : -1;
    return [...(Array.isArray(dramas) ? dramas : [])].sort((left, right) => {
        const leftTemporary = Boolean(left?.isTemporary);
        const rightTemporary = Boolean(right?.isTemporary);
        if (leftTemporary !== rightTemporary) {
            return leftTemporary ? -1 : 1;
        }

        if (sortMode === HIT_DRAMA_SORT_BY_IMPORT_TIME) {
            const leftCreatedAt = getHitDramaCreatedAtTimestamp(left);
            const rightCreatedAt = getHitDramaCreatedAtTimestamp(right);
            if (leftCreatedAt !== rightCreatedAt) {
                return (leftCreatedAt - rightCreatedAt) * sortDirection;
            }
            return (Number(left?.id || 0) - Number(right?.id || 0)) * sortDirection;
        }

        const leftOnlineTime = getHitDramaSortableOnlineTime(left?.online_time);
        const rightOnlineTime = getHitDramaSortableOnlineTime(right?.online_time);
        const leftEmpty = !leftOnlineTime;
        const rightEmpty = !rightOnlineTime;
        if (leftEmpty !== rightEmpty) {
            return leftEmpty ? 1 : -1;
        }
        if (leftOnlineTime !== rightOnlineTime) {
            return leftOnlineTime.localeCompare(rightOnlineTime, 'zh-Hans-CN') * sortDirection;
        }

        const leftCreatedAt = getHitDramaCreatedAtTimestamp(left);
        const rightCreatedAt = getHitDramaCreatedAtTimestamp(right);
        if (leftCreatedAt !== rightCreatedAt) {
            return (leftCreatedAt - rightCreatedAt) * sortDirection;
        }
        return (Number(left?.id || 0) - Number(right?.id || 0)) * sortDirection;
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = normalizeHitDramaValue(text);
    return div.innerHTML;
}

function normalizeHitDramaRecord(drama) {
    return {
        ...drama,
        drama_name: normalizeHitDramaValue(drama.drama_name),
        view_count: normalizeHitDramaValue(drama.view_count),
        opening_15_sentences: normalizeHitDramaValue(drama.opening_15_sentences),
        first_episode_script: normalizeHitDramaValue(drama.first_episode_script),
        online_time: normalizeHitDramaValue(drama.online_time),
        video_filename: normalizeHitDramaValue(drama.video_filename),
        isTemporary: Boolean(drama.isTemporary)
    };
}

function isTemporaryHitDramaId(dramaId) {
    return Number(dramaId) < 0;
}

function cacheHitDramas(dramas) {
    HIT_DRAMA_STATE.dramas = (dramas || []).map(normalizeHitDramaRecord);
    HIT_DRAMA_STATE.dramasById = new Map(
        HIT_DRAMA_STATE.dramas.map(drama => [drama.id, drama])
    );
    const validIds = new Set(HIT_DRAMA_STATE.dramas.map(drama => drama.id));
    HIT_DRAMA_STATE.rowHeights = new Map(
        Array.from(HIT_DRAMA_STATE.rowHeights.entries()).filter(([dramaId]) => validIds.has(dramaId))
    );
}

function prependHitDrama(drama) {
    if (!drama) {
        return null;
    }

    const normalizedDrama = normalizeHitDramaRecord(drama);

    HIT_DRAMA_STATE.dramas = [
        normalizedDrama,
        ...HIT_DRAMA_STATE.dramas.filter(item => item.id !== normalizedDrama.id)
    ];
    HIT_DRAMA_STATE.dramasById.set(normalizedDrama.id, normalizedDrama);
    return normalizedDrama;
}

function replaceHitDrama(oldId, drama) {
    const normalizedDrama = normalizeHitDramaRecord(drama);
    HIT_DRAMA_STATE.dramas = HIT_DRAMA_STATE.dramas.map(item =>
        item.id === oldId ? normalizedDrama : item
    );
    HIT_DRAMA_STATE.dramasById.delete(oldId);
    HIT_DRAMA_STATE.dramasById.set(normalizedDrama.id, normalizedDrama);
    if (HIT_DRAMA_STATE.rowHeights.has(oldId)) {
        HIT_DRAMA_STATE.rowHeights.set(normalizedDrama.id, HIT_DRAMA_STATE.rowHeights.get(oldId));
        HIT_DRAMA_STATE.rowHeights.delete(oldId);
    }
    return normalizedDrama;
}

function updateCachedHitDrama(drama) {
    const normalizedDrama = normalizeHitDramaRecord(drama);
    let found = false;
    HIT_DRAMA_STATE.dramas = HIT_DRAMA_STATE.dramas.map(item => {
        if (item.id === normalizedDrama.id) {
            found = true;
            return normalizedDrama;
        }
        return item;
    });
    if (!found) {
        HIT_DRAMA_STATE.dramas.push(normalizedDrama);
    }
    HIT_DRAMA_STATE.dramasById.set(normalizedDrama.id, normalizedDrama);
    return normalizedDrama;
}

function removeHitDramaFromState(dramaId) {
    HIT_DRAMA_STATE.dramas = HIT_DRAMA_STATE.dramas.filter(item => item.id !== dramaId);
    HIT_DRAMA_STATE.dramasById.delete(dramaId);
    HIT_DRAMA_STATE.rowHeights.delete(dramaId);
}

function getCachedHitDrama(dramaId) {
    return HIT_DRAMA_STATE.dramasById.get(dramaId) || null;
}

function getHitDramaRowHeight(dramaId) {
    return HIT_DRAMA_STATE.rowHeights.get(dramaId) || HIT_DRAMA_DEFAULT_ROW_HEIGHT;
}

function setHitDramaRowHeight(dramaId, nextHeight) {
    const resolvedHeight = Math.max(HIT_DRAMA_MIN_ROW_HEIGHT, Math.round(Number(nextHeight) || HIT_DRAMA_DEFAULT_ROW_HEIGHT));
    HIT_DRAMA_STATE.rowHeights.set(dramaId, resolvedHeight);
    document.querySelectorAll(`[data-hit-drama-row-height="${dramaId}"]`).forEach(element => {
        element.style.height = `${resolvedHeight}px`;
    });
}

function startHitDramaRowResize(event, dramaId) {
    event.preventDefault();
    event.stopPropagation();
    HIT_DRAMA_STATE.activeResize = {
        dramaId,
        startY: event.clientY,
        startHeight: getHitDramaRowHeight(dramaId)
    };
    document.addEventListener('mousemove', handleHitDramaRowResize);
    document.addEventListener('mouseup', stopHitDramaRowResize);
}

function handleHitDramaRowResize(event) {
    if (!HIT_DRAMA_STATE.activeResize) {
        return;
    }
    const nextHeight = HIT_DRAMA_STATE.activeResize.startHeight + (event.clientY - HIT_DRAMA_STATE.activeResize.startY);
    setHitDramaRowHeight(HIT_DRAMA_STATE.activeResize.dramaId, nextHeight);
}

function stopHitDramaRowResize() {
    HIT_DRAMA_STATE.activeResize = null;
    document.removeEventListener('mousemove', handleHitDramaRowResize);
    document.removeEventListener('mouseup', stopHitDramaRowResize);
}

function getHitDramaMeasureContext() {
    if (!hitDramaMeasureCanvasContext) {
        hitDramaMeasureCanvasContext = document.createElement('canvas').getContext('2d');
    }
    return hitDramaMeasureCanvasContext;
}

function isHitDramaPreviewPunctuation(char) {
    return /[\p{P}]/u.test(char);
}

function findHitDramaPreviewBreak(text, startIndex, reverse = false) {
    if (!text) {
        return -1;
    }

    const start = reverse ? Math.min(startIndex, text.length) - 1 : Math.max(startIndex, 0);
    if (reverse) {
        for (let index = start; index >= 0; index -= 1) {
            if (!isHitDramaPreviewPunctuation(text[index])) {
                continue;
            }
            let endIndex = index + 1;
            while (endIndex < text.length && isHitDramaPreviewPunctuation(text[endIndex])) {
                endIndex += 1;
            }
            return endIndex;
        }
        return -1;
    }

    for (let index = start; index < text.length; index += 1) {
        if (!isHitDramaPreviewPunctuation(text[index])) {
            continue;
        }
        let endIndex = index + 1;
        while (endIndex < text.length && isHitDramaPreviewPunctuation(text[endIndex])) {
            endIndex += 1;
        }
        return endIndex;
    }

    return -1;
}

function getHitDramaPreviewThreshold(element) {
    const context = getHitDramaMeasureContext();
    const style = window.getComputedStyle(element);
    context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    const sampleWidth = context.measureText('测').width || 13;
    const availableWidth = Math.max(40, element.clientWidth - 8);
    const charsPerLine = Math.max(1, Math.floor(availableWidth / Math.max(sampleWidth, 1)));
    return Math.max(1, Math.floor(charsPerLine * HIT_DRAMA_PREVIEW_BREAK_RATIO));
}

function formatHitDramaPreviewText(rawText, threshold) {
    const sourceText = normalizeHitDramaValue(rawText).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    if (!sourceText.trim()) {
        return '';
    }

    const formattedBlocks = [];
    sourceText.split('\n').forEach(rawBlock => {
        const block = rawBlock.trim();
        if (!block) {
            return;
        }

        const wrappedLines = [];
        let remaining = block;
        while (remaining.length > threshold) {
            let splitAt = findHitDramaPreviewBreak(remaining, threshold, false);
            if (splitAt === -1) {
                splitAt = findHitDramaPreviewBreak(remaining, threshold, true);
            }
            if (splitAt === -1) {
                splitAt = threshold;
            }
            wrappedLines.push(remaining.slice(0, splitAt).trim());
            remaining = remaining.slice(splitAt).trimStart();
        }

        if (remaining) {
            wrappedLines.push(remaining.trim());
        }

        formattedBlocks.push(wrappedLines.join('\n'));
    });

    return formattedBlocks.join('\n');
}

function refreshHitDramaPreviewText(root = document) {
    root.querySelectorAll('.hit-drama-resizable-block[data-raw-text]').forEach(element => {
        const rawText = element.dataset.rawText || '';
        const formattedText = formatHitDramaPreviewText(rawText, getHitDramaPreviewThreshold(element));
        if (!formattedText) {
            element.innerHTML = '<span class="hit-drama-placeholder">-</span>';
            return;
        }
        element.textContent = formattedText;
    });
}

if (!window.__hitDramaPreviewResizeBound) {
    window.addEventListener('resize', () => {
        const tableBody = document.getElementById('hitDramasTableBody');
        if (tableBody) {
            refreshHitDramaPreviewText(tableBody);
        }
    });
    window.__hitDramaPreviewResizeBound = true;
}

async function hitDramaApiRequest(url, options = {}) {
    if (typeof apiRequest === 'function') {
        return apiRequest(url, options);
    }

    const token = localStorage.getItem('authToken');
    return fetch(url, {
        ...options,
        headers: {
            ...(options.headers || {}),
            ...(token ? { Authorization: `Bearer ${token}` } : {})
        }
    });
}

function hitDramaToast(message, type = 'success') {
    if (typeof window.showToast === 'function') {
        window.showToast(message, type);
        return;
    }

    const toast = document.createElement('div');
    toast.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: ${type === 'error' ? '#dc2626' : '#16a34a'};
        color: #fff;
        padding: 14px 18px;
        border-radius: 8px;
        z-index: 10000;
        box-shadow: 0 12px 30px rgba(0, 0, 0, 0.28);
    `;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

function buildHitDramasShell() {
    return `
        <div class="storyboard-table-container hit-dramas-page ${HIT_DRAMA_STATE.standaloneView ? 'hit-dramas-page-standalone' : 'hit-dramas-page-embedded'}">
            <div class="storyboard-table-actions hit-dramas-actions">
                <div class="hit-dramas-toolbar-copy">
                    ${HIT_DRAMA_STATE.standaloneView ? `
                        <button class="secondary-button hit-drama-back-button" onclick="loadView('my-scripts')">\u8fd4\u56de\u5267\u672c</button>
                    ` : ''}
                    <div class="hit-dramas-toolbar-title">\u7206\u6b3e\u5e93</div>
                    <div class="hit-dramas-toolbar-meta" id="hitDramaToolbarMeta">\u6b63\u5728\u52a0\u8f7d...</div>
                </div>
                <div class="hit-dramas-toolbar-actions">
                    <label class="hit-drama-sort-control">
                        <span class="hit-drama-sort-label">\u6392\u5e8f</span>
                        <select class="hit-drama-sort-select" id="hitDramaSortMode" onchange="changeHitDramaSortMode(this.value)">
                            <option value="${HIT_DRAMA_SORT_BY_ONLINE_TIME}" ${HIT_DRAMA_STATE.sortMode === HIT_DRAMA_SORT_BY_ONLINE_TIME ? 'selected' : ''}>\u4e0a\u7ebf\u65f6\u95f4</option>
                            <option value="${HIT_DRAMA_SORT_BY_IMPORT_TIME}" ${HIT_DRAMA_STATE.sortMode === HIT_DRAMA_SORT_BY_IMPORT_TIME ? 'selected' : ''}>\u5bfc\u5165\u65f6\u95f4</option>
                        </select>
                    </label>
                    <button class="secondary-button hit-drama-sort-order-button" onclick="toggleHitDramaSortDirection()">
                        ${getHitDramaSortDirectionLabel()}
                    </button>
                    <button class="primary-button" onclick="addHitDramaRow()">\u65b0\u589e\u8bb0\u5f55</button>
                    <button class="secondary-button" onclick="showImportExcelModal()">\u5bfc\u5165 Excel</button>
                    <button class="secondary-button" onclick="showHitDramaHistory()">\u67e5\u770b\u7f16\u8f91\u8bb0\u5f55</button>
                </div>
            </div>
            <div class="storyboard-table-wrapper hit-dramas-wrapper">
                <table class="storyboard-edit-table hit-dramas-table">
                    <thead>
                        <tr>
                            <th style="width: 180px; min-width: 180px;">\u5267\u540d</th>
                            <th style="width: 120px; min-width: 120px;">\u64ad\u653e\u91cf</th>
                            <th style="width: 360px; min-width: 360px;">\u5f00\u5934 15 \u53e5</th>
                            <th style="width: 420px; min-width: 420px;">\u7b2c\u4e00\u96c6\u6587\u6848</th>
                            <th style="width: 140px; min-width: 140px;">\u4e0a\u7ebf\u65f6\u95f4</th>
                            <th style="width: 260px; min-width: 260px;">\u89c6\u9891</th>
                            <th style="width: 220px; min-width: 220px;">\u64cd\u4f5c</th>
                        </tr>
                    </thead>
                    <tbody id="hitDramasTableBody">
                        <tr class="storyboard-row">
                            <td colspan="7" class="empty-row">\u52a0\u8f7d\u4e2d...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    `;
}

function changeHitDramaSortMode(nextMode) {
    setHitDramaSortMode(nextMode);
    refreshHitDramaSortControls();
    renderHitDramaRows(getSortedHitDramaList(HIT_DRAMA_STATE.dramas));
}

function toggleHitDramaSortDirection() {
    setHitDramaSortDirection(
        HIT_DRAMA_STATE.sortDirection === HIT_DRAMA_SORT_ASC
            ? HIT_DRAMA_SORT_DESC
            : HIT_DRAMA_SORT_ASC
    );
    refreshHitDramaSortControls();
    renderHitDramaRows(getSortedHitDramaList(HIT_DRAMA_STATE.dramas));
}

function refreshHitDramaSortControls() {
    const sortSelect = document.getElementById('hitDramaSortMode');
    if (sortSelect) {
        sortSelect.value = HIT_DRAMA_STATE.sortMode;
    }
    const directionButton = document.querySelector('.hit-drama-sort-order-button');
    if (directionButton) {
        directionButton.textContent = getHitDramaSortDirectionLabel();
    }
}

function updateHitDramaToolbarMeta(dramas) {
    const meta = document.getElementById('hitDramaToolbarMeta');
    if (!meta) {
        return;
    }
    meta.textContent = `\u5168\u5c40\u5171\u4eab\u5e93 \u00b7 \u5171 ${Array.isArray(dramas) ? dramas.length : 0} \u6761\u8bb0\u5f55`;
}

function renderReadCell(field, value, cellClass = '') {
    const text = normalizeHitDramaValue(value);
    return `
        <td class="hit-drama-cell ${cellClass}">
            <div class="hit-drama-cell-text" data-field="${field}">${text ? escapeHtml(text) : '<span class="hit-drama-placeholder">-</span>'}</div>
        </td>
    `;
}

function renderLongTextCell(field, value, dramaId) {
    const text = normalizeHitDramaValue(value);
    return `
        <td class="hit-drama-cell hit-drama-cell-long">
            <div
                class="hit-drama-cell-text hit-drama-resizable-block"
                data-field="${field}"
                data-raw-text="${escapeHtml(text)}"
                data-hit-drama-row-height="${dramaId}"
                style="height: ${getHitDramaRowHeight(dramaId)}px;"
            >${text ? escapeHtml(text) : '<span class="hit-drama-placeholder">-</span>'}</div>
        </td>
    `;
}

function renderVideoCell(drama) {
    if (!drama.video_filename) {
        return `
            <td class="hit-drama-cell hit-drama-video-cell">
                <span class="hit-drama-placeholder">\u6682\u65e0\u89c6\u9891</span>
            </td>
        `;
    }

    return `
        <td class="hit-drama-cell hit-drama-video-cell">
            <video
                class="hit-drama-inline-video"
                src="${escapeHtml(buildHitDramaVideoUrl(drama.video_filename))}"
                preload="metadata"
                controls
                playsinline
                onplay="pauseOtherHitDramaInlineVideos(this)"
            ></video>
        </td>
    `;
}

function renderActionsCell(drama) {
    return `
        <td class="hit-drama-cell hit-drama-actions-cell">
            <div class="hit-drama-row-actions">
                <button class="secondary-button hit-drama-button" onclick="editHitDramaRow(${drama.id})">\u7f16\u8f91</button>
                <button class="secondary-button hit-drama-button" onclick="uploadHitDramaVideo(${drama.id})">\u4e0a\u4f20\u89c6\u9891</button>
                <button class="secondary-button hit-drama-button hit-drama-button-danger" onclick='deleteHitDrama(${drama.id}, ${JSON.stringify(drama.drama_name)})'>\u5220\u9664</button>
            </div>
        </td>
    `;
}

function renderHitDramaResizeRow(dramaId) {
    return `
        <tr class="hit-drama-resize-row" data-hit-drama-resize-for="${dramaId}">
            <td colspan="7" class="hit-drama-resize-cell">
                <button class="hit-drama-row-resizer" type="button" onmousedown="startHitDramaRowResize(event, ${dramaId})" title="\u62d6\u52a8\u8c03\u6574\u884c\u9ad8"></button>
            </td>
        </tr>
    `;
}

function renderHitDramaRow(drama) {
    return `
        <tr class="storyboard-row hit-drama-row" id="drama-row-${drama.id}" data-drama-id="${drama.id}">
            ${renderReadCell('drama_name', drama.drama_name, 'hit-drama-cell-compact')}
            ${renderReadCell('view_count', drama.view_count, 'hit-drama-cell-compact')}
            ${renderLongTextCell('opening_15_sentences', drama.opening_15_sentences, drama.id)}
            ${renderLongTextCell('first_episode_script', drama.first_episode_script, drama.id)}
            ${renderReadCell('online_time', drama.online_time, 'hit-drama-cell-compact')}
            ${renderVideoCell(drama)}
            ${renderActionsCell(drama)}
        </tr>
        ${renderHitDramaResizeRow(drama.id)}
    `;
}

function renderHitDramaRows(dramas) {
    const tbody = document.getElementById('hitDramasTableBody');
    if (!tbody) {
        return;
    }

    if (!Array.isArray(dramas) || dramas.length === 0) {
        tbody.innerHTML = `
            <tr class="storyboard-row">
                <td colspan="7" class="empty-row">\u6682\u65e0\u8bb0\u5f55</td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = dramas.map(renderHitDramaRow).join('');
    refreshHitDramaPreviewText(tbody);
}

async function loadHitDramas() {
    const content = document.getElementById('hitDramasContainer') || document.getElementById('content');
    if (!content) {
        return;
    }

    HIT_DRAMA_STATE.standaloneView = content.id === 'content';
    syncHitDramaSortSettings();
    content.innerHTML = buildHitDramasShell();
    refreshHitDramaSortControls();

    try {
        const response = await hitDramaApiRequest('/api/hit-dramas');
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || '\u52a0\u8f7d\u5931\u8d25');
        }

        const dramas = await response.json();
        cacheHitDramas(dramas);
        updateHitDramaToolbarMeta(dramas);
        renderHitDramaRows(getSortedHitDramaList(HIT_DRAMA_STATE.dramas));
    } catch (error) {
        console.error('Failed to load hit dramas:', error);
        updateHitDramaToolbarMeta([]);
        renderHitDramaRows([]);
        hitDramaToast(error.message || '\u52a0\u8f7d\u5931\u8d25', 'error');
    }
}

function buildHitDramaVideoUrl(filename) {
    return `${window.location.origin}/files/${encodeURIComponent(filename)}`;
}

function pauseOtherHitDramaInlineVideos(activeVideo) {
    document.querySelectorAll('.hit-drama-inline-video').forEach(video => {
        if (video !== activeVideo && !video.paused) {
            video.pause();
        }
    });
}

function renderHitDramaEditRow(drama) {
    return `
        <td class="hit-drama-cell hit-drama-cell-compact">
            <input class="table-input hit-drama-table-input" type="text" data-field="drama_name" value="${escapeHtml(drama.drama_name)}">
        </td>
        <td class="hit-drama-cell hit-drama-cell-compact">
            <input class="table-input hit-drama-table-input" type="text" data-field="view_count" value="${escapeHtml(drama.view_count)}">
        </td>
        <td class="hit-drama-cell hit-drama-cell-long">
            <textarea class="table-textarea hit-drama-table-textarea" rows="8" data-field="opening_15_sentences" data-hit-drama-row-height="${drama.id}" style="height: ${getHitDramaRowHeight(drama.id)}px;">${escapeHtml(drama.opening_15_sentences)}</textarea>
        </td>
        <td class="hit-drama-cell hit-drama-cell-long">
            <textarea class="table-textarea hit-drama-table-textarea" rows="8" data-field="first_episode_script" data-hit-drama-row-height="${drama.id}" style="height: ${getHitDramaRowHeight(drama.id)}px;">${escapeHtml(drama.first_episode_script)}</textarea>
        </td>
        <td class="hit-drama-cell hit-drama-cell-compact">
            <input class="table-input hit-drama-table-input" type="text" data-field="online_time" value="${escapeHtml(drama.online_time)}">
        </td>
        ${renderVideoCell(drama)}
        <td class="hit-drama-cell hit-drama-actions-cell">
            <div class="hit-drama-row-actions">
                <button class="primary-button hit-drama-button" onclick="saveHitDramaRow(${drama.id})">\u4fdd\u5b58</button>
                <button class="secondary-button hit-drama-button" onclick="cancelEditHitDramaRow(${drama.id})">\u53d6\u6d88</button>
            </div>
        </td>
    `;
}

function editHitDramaRow(dramaId) {
    const drama = getCachedHitDrama(dramaId);
    const row = document.getElementById(`drama-row-${dramaId}`);
    if (!drama || !row) {
        hitDramaToast('\u8bb0\u5f55\u4e0d\u5b58\u5728', 'error');
        return;
    }
    row.classList.add('hit-drama-row-editing');
    row.innerHTML = renderHitDramaEditRow(drama);
}

function restoreHitDramaRow(dramaId) {
    const drama = getCachedHitDrama(dramaId);
    const row = document.getElementById(`drama-row-${dramaId}`);
    if (!drama || !row) {
        return;
    }
    const resizeRow = row.nextElementSibling && row.nextElementSibling.classList.contains('hit-drama-resize-row')
        ? row.nextElementSibling
        : null;
    row.insertAdjacentHTML('beforebegin', renderHitDramaRow(drama));
    row.remove();
    resizeRow?.remove();
    refreshHitDramaPreviewText(document.getElementById('hitDramasTableBody') || document);
}

async function saveHitDramaRow(dramaId) {
    const row = document.getElementById(`drama-row-${dramaId}`);
    if (!row) {
        return;
    }

    const updateData = {};
    row.querySelectorAll('[data-field]').forEach(field => {
        const fieldName = field.dataset.field;
        updateData[fieldName] = normalizeHitDramaValue(field.value).trim();
    });

    try {
        if (Object.prototype.hasOwnProperty.call(updateData, 'online_time')) {
            updateData.online_time = normalizeHitDramaOnlineTimeInput(updateData.online_time);
        }
    } catch (error) {
        hitDramaToast(error.message || '\u4e0a\u7ebf\u65f6\u95f4\u683c\u5f0f\u9519\u8bef', 'error');
        row.querySelector('[data-field="online_time"]')?.focus();
        return;
    }

    try {
        const isTemporary = isTemporaryHitDramaId(dramaId);
        const response = await hitDramaApiRequest(
            isTemporary ? '/api/hit-dramas' : `/api/hit-dramas/${dramaId}`,
            {
            method: isTemporary ? 'POST' : 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updateData)
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || '\u4fdd\u5b58\u5931\u8d25');
        }

        const savedDrama = await response.json();
        if (isTemporary) {
            replaceHitDrama(dramaId, savedDrama);
        } else {
            updateCachedHitDrama(savedDrama);
        }

        hitDramaToast('\u4fdd\u5b58\u6210\u529f');
        updateHitDramaToolbarMeta(HIT_DRAMA_STATE.dramas);
        renderHitDramaRows(getSortedHitDramaList(HIT_DRAMA_STATE.dramas));
    } catch (error) {
        console.error('Failed to save hit drama:', error);
        hitDramaToast(error.message || '\u4fdd\u5b58\u5931\u8d25', 'error');
    }
}

function cancelEditHitDramaRow(dramaId) {
    if (isTemporaryHitDramaId(dramaId)) {
        removeHitDramaFromState(dramaId);
        updateHitDramaToolbarMeta(HIT_DRAMA_STATE.dramas);
        renderHitDramaRows(getSortedHitDramaList(HIT_DRAMA_STATE.dramas));
        return;
    }
    restoreHitDramaRow(dramaId);
}

async function deleteHitDrama(dramaId, dramaName) {
    const targetName = dramaName || '\u8fd9\u6761\u8bb0\u5f55';
    const confirmed = await showHitDramaConfirmModal(
        `\u786e\u5b9a\u8981\u5220\u9664\u300c${targetName}\u300d\u5417\uff1f`,
        '\u5220\u9664\u8bb0\u5f55'
    );
    if (!confirmed) {
        return;
    }

    if (isTemporaryHitDramaId(dramaId)) {
        removeHitDramaFromState(dramaId);
        updateHitDramaToolbarMeta(HIT_DRAMA_STATE.dramas);
        renderHitDramaRows(getSortedHitDramaList(HIT_DRAMA_STATE.dramas));
        return;
    }

    try {
        const response = await hitDramaApiRequest(`/api/hit-dramas/${dramaId}`, { method: 'DELETE' });
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || '\u5220\u9664\u5931\u8d25');
        }

        hitDramaToast('\u5220\u9664\u6210\u529f');
        await loadHitDramas();
    } catch (error) {
        console.error('Failed to delete hit drama:', error);
        hitDramaToast(error.message || '\u5220\u9664\u5931\u8d25', 'error');
    }
}

function createHitDramaModal({ title, bodyHtml, width = '760px', extraClass = '' }) {
    const modal = document.createElement('div');
    modal.className = `modal active hit-drama-modal ${extraClass}`.trim();
    modal.innerHTML = `
        <div class="modal-content" style="width: min(${width}, 92vw); max-width: 92vw;">
            <div class="modal-header">
                <h3>${escapeHtml(title)}</h3>
                <button class="modal-close" type="button" onclick="this.closest('.hit-drama-modal').remove()">&times;</button>
            </div>
            <div class="modal-body">
                ${bodyHtml}
            </div>
        </div>
    `;

    modal.addEventListener('click', event => {
        if (event.target === modal) {
            modal.remove();
        }
    });

    return modal;
}

function getOpenHitDramaModal() {
    return document.querySelector('.hit-drama-modal');
}

function showHitDramaConfirmModal(message, title = '\u786e\u8ba4') {
    if (typeof window.showConfirmModal === 'function') {
        return window.showConfirmModal(message, title);
    }

    return new Promise(resolve => {
        const modal = createHitDramaModal({
            title,
            width: '520px',
            extraClass: 'hit-drama-modal-compact hit-drama-modal-centered',
            bodyHtml: `
                <div class="confirm-message">${escapeHtml(message)}</div>
                <div class="hit-drama-modal-actions">
                    <button class="secondary-button" type="button" data-hit-drama-confirm="cancel">\u53d6\u6d88</button>
                    <button class="primary-button" type="button" data-hit-drama-confirm="ok">\u786e\u5b9a</button>
                </div>
            `
        });

        const cleanup = result => {
            modal.remove();
            resolve(result);
        };

        modal.querySelector('[data-hit-drama-confirm="cancel"]')?.addEventListener('click', () => cleanup(false));
        modal.querySelector('[data-hit-drama-confirm="ok"]')?.addEventListener('click', () => cleanup(true));
        document.body.appendChild(modal);
    });
}

function showImportExcelModal() {
    const modal = createHitDramaModal({
        title: '\u5bfc\u5165 Excel',
        width: '640px',
        extraClass: 'hit-drama-modal-compact hit-drama-modal-centered hit-drama-import-modal',
        bodyHtml: `
            <div class="hit-drama-import-panel">
                <div class="form-group is-wide">
                    <label class="form-label" for="excelFileInput">\u9009\u62e9 Excel \u6587\u4ef6</label>
                    <input class="form-input hit-drama-file-input" type="file" id="excelFileInput" accept=".xlsx">
                </div>
                <div class="form-group is-wide">
                    <label class="form-label">\u5bfc\u5165\u6a21\u5f0f</label>
                    <div class="hit-drama-import-mode-group">
                        <label class="hit-drama-import-mode-option">
                            <input type="radio" name="hitDramaImportMode" value="append" checked>
                            <span>\u8ffd\u52a0</span>
                        </label>
                        <label class="hit-drama-import-mode-option">
                            <input type="radio" name="hitDramaImportMode" value="overwrite">
                            <span>\u8986\u76d6</span>
                        </label>
                    </div>
                </div>
            </div>
            <div class="hit-drama-modal-actions">
                <button class="secondary-button" type="button" onclick="this.closest('.hit-drama-modal').remove()">\u53d6\u6d88</button>
                <button class="primary-button" type="button" onclick="importExcelFile()">\u5bfc\u5165</button>
            </div>
        `
    });

    document.body.appendChild(modal);
}

async function addHitDramaRow() {
    const drama = prependHitDrama({
        id: HIT_DRAMA_STATE.nextTempId--,
        drama_name: '',
        view_count: '',
        opening_15_sentences: '',
        first_episode_script: '',
        online_time: '',
        video_filename: '',
        isTemporary: true
    });
    updateHitDramaToolbarMeta(HIT_DRAMA_STATE.dramas);
    renderHitDramaRows(getSortedHitDramaList(HIT_DRAMA_STATE.dramas));
    if (drama) {
        editHitDramaRow(drama.id);
        document.getElementById(`drama-row-${drama.id}`)?.scrollIntoView({
            behavior: 'smooth',
            block: 'start'
        });
    }
}

async function importExcelFile() {
    const fileInput = document.getElementById('excelFileInput');
    const file = fileInput?.files?.[0];
    if (!file) {
        hitDramaToast('\u8bf7\u9009\u62e9\u6587\u4ef6', 'error');
        return;
    }

    const importMode = document.querySelector('input[name="hitDramaImportMode"]:checked')?.value || 'append';
    if (importMode === 'overwrite') {
        const confirmed = await showHitDramaConfirmModal(
            '\u8986\u76d6\u5bfc\u5165\u4f1a\u6e05\u7a7a\u5f53\u524d\u7206\u6b3e\u5e93\u4e3b\u8bb0\u5f55\u548c\u7f16\u8f91\u8bb0\u5f55\uff0c\u786e\u5b9a\u7ee7\u7eed\u5417\uff1f',
            '\u8986\u76d6\u5bfc\u5165'
        );
        if (!confirmed) {
            return;
        }
    }

    const formData = new FormData();
    formData.append('file', file);
    formData.append('import_mode', importMode);

    try {
        const response = await hitDramaApiRequest('/api/hit-dramas/import-excel', {
            method: 'POST',
            body: formData
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || '\u5bfc\u5165\u5931\u8d25');
        }

        const result = await response.json();
        hitDramaToast(result.message || '\u5bfc\u5165\u6210\u529f');
        getOpenHitDramaModal()?.remove();
        await loadHitDramas();
    } catch (error) {
        console.error('Failed to import excel:', error);
        hitDramaToast(error.message || '\u5bfc\u5165\u5931\u8d25', 'error');
    }
}

function uploadHitDramaVideo(dramaId) {
    const modal = createHitDramaModal({
        title: '\u4e0a\u4f20\u89c6\u9891',
        width: '700px',
        extraClass: 'hit-drama-modal-compact hit-drama-modal-centered',
        bodyHtml: `
            <div class="form-group is-wide">
                <label class="form-label" for="videoFileInput">\u9009\u62e9\u89c6\u9891\u6587\u4ef6</label>
                <input class="form-input hit-drama-file-input" type="file" id="videoFileInput" accept="video/*">
            </div>
            <div class="hit-drama-modal-actions">
                <button class="secondary-button" type="button" onclick="this.closest('.hit-drama-modal').remove()">\u53d6\u6d88</button>
                <button class="primary-button" type="button" onclick="uploadVideoFile(${dramaId})">\u4e0a\u4f20</button>
            </div>
        `
    });

    document.body.appendChild(modal);
}

async function uploadVideoFile(dramaId) {
    const fileInput = document.getElementById('videoFileInput');
    const file = fileInput?.files?.[0];
    if (!file) {
        hitDramaToast('\u8bf7\u9009\u62e9\u6587\u4ef6', 'error');
        return;
    }

    const formData = new FormData();
    formData.append('drama_id', dramaId);
    formData.append('file', file);

    try {
        hitDramaToast('\u4e0a\u4f20\u4e2d...', 'info');

        const response = await hitDramaApiRequest('/api/hit-dramas/upload-video', {
            method: 'POST',
            body: formData
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || '\u4e0a\u4f20\u5931\u8d25');
        }

        hitDramaToast('\u4e0a\u4f20\u6210\u529f');
        getOpenHitDramaModal()?.remove();
        await loadHitDramas();
    } catch (error) {
        console.error('Failed to upload video:', error);
        hitDramaToast(error.message || '\u4e0a\u4f20\u5931\u8d25', 'error');
    }
}

function renderHitDramaHistoryItem(history) {
    const actionLabel = history.action_type === 'create'
        ? '\u521b\u5efa'
        : history.action_type === 'update'
            ? '\u4fee\u6539'
            : '\u5220\u9664';
    const diffHtml = history.field_name && history.old_value !== null && history.new_value !== null
        ? generateDiffHtml(history.old_value, history.new_value)
        : '';

    return `
        <div class="hit-drama-history-card">
            <div class="hit-drama-history-meta">
                <div>
                    <div class="hit-drama-history-title">${escapeHtml(history.drama_name || '\u672a\u77e5\u5267\u672c')}</div>
                    <div class="hit-drama-history-submeta">
                        <span class="hit-drama-history-tag">${actionLabel}</span>
                        <span>${new Date(history.edited_at).toLocaleString()}</span>
                        <span>${escapeHtml(history.edited_by)}</span>
                    </div>
                </div>
            </div>
            ${history.field_name ? `
                <div class="hit-drama-history-field">\u5b57\u6bb5\uff1a${escapeHtml(history.field_name)}</div>
                ${diffHtml}
            ` : `
                <div class="hit-drama-history-note">${escapeHtml(history.new_value || '')}</div>
            `}
        </div>
    `;
}

async function showHitDramaHistory() {
    const modal = createHitDramaModal({
        title: '\u7f16\u8f91\u8bb0\u5f55',
        width: '1180px',
        extraClass: 'hit-drama-history-modal',
        bodyHtml: `
            <div id="historyContent" class="hit-drama-history-content">
                <div class="hit-drama-history-empty">\u52a0\u8f7d\u4e2d...</div>
            </div>
        `
    });

    document.body.appendChild(modal);
    await filterHitDramaHistory();
}

async function filterHitDramaHistory() {
    const historyContent = document.getElementById('historyContent');
    if (!historyContent) {
        return;
    }

    try {
        const response = await hitDramaApiRequest('/api/hit-dramas/history');
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || '\u52a0\u8f7d\u5931\u8d25');
        }

        const histories = await response.json();
        if (!Array.isArray(histories)) {
            throw new Error('\u8fd4\u56de\u683c\u5f0f\u9519\u8bef');
        }

        if (histories.length === 0) {
            historyContent.innerHTML = '<div class="hit-drama-history-empty">\u6682\u65e0\u8bb0\u5f55</div>';
            return;
        }

        historyContent.innerHTML = histories.map(renderHitDramaHistoryItem).join('');
    } catch (error) {
        console.error('Failed to load history:', error);
        historyContent.innerHTML = '<div class="hit-drama-history-empty">\u52a0\u8f7d\u5931\u8d25</div>';
        hitDramaToast(error.message || '\u52a0\u8f7d\u5931\u8d25', 'error');
    }
}

function generateDiffHtml(oldText, newText) {
    const diffs = computeDiff(oldText, newText);

    let oldHtml = '<div class="hit-drama-diff-panel"><div class="hit-drama-diff-title hit-drama-diff-title-old">\u65e7\u503c</div><pre class="hit-drama-diff-content">';
    let newHtml = '<div class="hit-drama-diff-panel"><div class="hit-drama-diff-title hit-drama-diff-title-new">\u65b0\u503c</div><pre class="hit-drama-diff-content">';

    diffs.forEach(diff => {
        const text = escapeHtml(diff.text);
        if (diff.type === 'equal') {
            oldHtml += text;
            newHtml += text;
        } else if (diff.type === 'delete') {
            oldHtml += `<span class="hit-drama-diff-delete">${text}</span>`;
        } else if (diff.type === 'insert') {
            newHtml += `<span class="hit-drama-diff-insert">${text}</span>`;
        }
    });

    oldHtml += '</pre></div>';
    newHtml += '</pre></div>';
    return `<div class="hit-drama-diff-grid">${oldHtml}${newHtml}</div>`;
}

function computeDiff(oldText, newText) {
    const oldValue = normalizeHitDramaValue(oldText);
    const newValue = normalizeHitDramaValue(newText);

    if (oldValue === newValue) {
        return [{ type: 'equal', text: oldValue }];
    }

    let prefix = 0;
    const maxPrefix = Math.min(oldValue.length, newValue.length);
    while (prefix < maxPrefix && oldValue[prefix] === newValue[prefix]) {
        prefix += 1;
    }

    let suffix = 0;
    const oldRemaining = oldValue.length - prefix;
    const newRemaining = newValue.length - prefix;
    while (
        suffix < oldRemaining &&
        suffix < newRemaining &&
        oldValue[oldValue.length - 1 - suffix] === newValue[newValue.length - 1 - suffix]
    ) {
        suffix += 1;
    }

    const oldMiddle = oldValue.slice(prefix, oldValue.length - suffix);
    const newMiddle = newValue.slice(prefix, newValue.length - suffix);
    const trailing = suffix > 0 ? oldValue.slice(oldValue.length - suffix) : '';
    const diffs = [];

    if (prefix > 0) {
        diffs.push({ type: 'equal', text: oldValue.slice(0, prefix) });
    }
    if (oldMiddle) {
        diffs.push({ type: 'delete', text: oldMiddle });
    }
    if (newMiddle) {
        diffs.push({ type: 'insert', text: newMiddle });
    }
    if (trailing) {
        diffs.push({ type: 'equal', text: trailing });
    }

    return diffs;
}
