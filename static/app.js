// MacAttack-Web Frontend JavaScript

// State
let playerState = {
    url: '',
    mac: '',
    token: '',
    portal_type: '',
    categories: { live: [], vod: [], series: [] },
    currentCategory: 'live'
};

// Tab Navigation
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(btn.dataset.tab).classList.add('active');
    });
});

// ============== ATTACK TAB ==============

let attackInterval = null;

document.getElementById('btn-start').addEventListener('click', async () => {
    const url = document.getElementById('attack-url').value.trim();
    if (!url) {
        alert('Please enter a portal URL');
        return;
    }
    
    const res = await fetch('/api/attack/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url })
    });
    const data = await res.json();
    
    if (data.success) {
        document.getElementById('btn-start').disabled = true;
        document.getElementById('btn-pause').disabled = false;
        document.getElementById('btn-stop').disabled = false;
        startStatusPolling();
    } else {
        alert(data.error);
    }
});

document.getElementById('btn-pause').addEventListener('click', async () => {
    const res = await fetch('/api/attack/pause', { method: 'POST' });
    const data = await res.json();
    document.getElementById('btn-pause').textContent = data.paused ? '▶ Resume' : '⏸ Pause';
});

document.getElementById('btn-stop').addEventListener('click', async () => {
    await fetch('/api/attack/stop', { method: 'POST' });
    stopStatusPolling();
    document.getElementById('btn-start').disabled = false;
    document.getElementById('btn-pause').disabled = true;
    document.getElementById('btn-stop').disabled = true;
    document.getElementById('btn-pause').textContent = '⏸ Pause';
});

function startStatusPolling() {
    attackInterval = setInterval(updateAttackStatus, 500);
}

function stopStatusPolling() {
    if (attackInterval) {
        clearInterval(attackInterval);
        attackInterval = null;
    }
}

async function updateAttackStatus() {
    const res = await fetch('/api/attack/status');
    const data = await res.json();
    
    document.getElementById('stat-tested').textContent = data.tested;
    document.getElementById('stat-hits').textContent = data.hits;
    document.getElementById('stat-errors').textContent = data.errors;
    document.getElementById('current-mac').textContent = data.current_mac || '-';
    
    // Format time
    const mins = Math.floor(data.elapsed / 60);
    const secs = data.elapsed % 60;
    document.getElementById('stat-time').textContent = 
        `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    
    // Update found list
    const foundList = document.getElementById('found-list');
    foundList.innerHTML = data.found_macs.map(m => 
        `<div class="log-entry success">${m.mac} - ${m.expiry}</div>`
    ).join('');
    foundList.scrollTop = foundList.scrollHeight;
    
    // Update log
    const logBox = document.getElementById('attack-log');
    logBox.innerHTML = data.logs.map(l => 
        `<div class="log-entry ${l.level}"><span class="time">${l.time}</span>${l.message}</div>`
    ).join('');
    logBox.scrollTop = logBox.scrollHeight;
    
    if (!data.running) {
        stopStatusPolling();
        document.getElementById('btn-start').disabled = false;
        document.getElementById('btn-pause').disabled = true;
        document.getElementById('btn-stop').disabled = true;
    }
}


// ============== PLAYER TAB ==============

document.querySelectorAll('.cat-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.cat-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        playerState.currentCategory = btn.dataset.cat;
        renderCategories();
    });
});

document.getElementById('btn-connect').addEventListener('click', async () => {
    const url = document.getElementById('player-url').value.trim();
    const mac = document.getElementById('player-mac').value.trim();
    const proxy = document.getElementById('player-proxy').value.trim();
    
    if (!url || !mac) {
        alert('Please enter URL and MAC address');
        return;
    }
    
    document.getElementById('btn-connect').textContent = 'Connecting...';
    document.getElementById('btn-connect').disabled = true;
    
    try {
        const res = await fetch('/api/player/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, mac, proxy })
        });
        const data = await res.json();
        
        if (data.success) {
            playerState.url = url;
            playerState.mac = mac;
            playerState.token = data.token;
            playerState.portal_type = data.portal_type;
            playerState.categories.live = data.live;
            playerState.categories.vod = data.vod;
            playerState.categories.series = data.series;
            renderCategories();
        } else {
            alert('Connection failed: ' + data.error);
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
    
    document.getElementById('btn-connect').textContent = 'Connect';
    document.getElementById('btn-connect').disabled = false;
});

function renderCategories() {
    const list = document.getElementById('category-list');
    const cats = playerState.categories[playerState.currentCategory] || [];
    
    list.innerHTML = cats.map(c => 
        `<div class="list-item" data-id="${c.id}" data-type="${playerState.currentCategory}">${c.name}</div>`
    ).join('');
    
    list.querySelectorAll('.list-item').forEach(item => {
        item.addEventListener('click', () => loadChannels(item.dataset.id, item.dataset.type));
    });
}

async function loadChannels(categoryId, categoryType) {
    const typeMap = { live: 'IPTV', vod: 'VOD', series: 'Series' };
    const proxy = document.getElementById('player-proxy').value.trim();
    
    const res = await fetch('/api/player/channels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            url: playerState.url,
            mac: playerState.mac,
            token: playerState.token,
            portal_type: playerState.portal_type,
            category_type: typeMap[categoryType],
            category_id: categoryId,
            proxy
        })
    });
    const data = await res.json();
    
    if (data.success) {
        const list = document.getElementById('channel-list');
        list.innerHTML = data.channels.map(ch => 
            `<div class="list-item" data-cmd="${ch.cmd || ''}" data-type="${categoryType}">${ch.name}</div>`
        ).join('');
        
        list.querySelectorAll('.list-item').forEach(item => {
            item.addEventListener('click', () => getStreamUrl(item.dataset.cmd, item.dataset.type));
        });
    }
}

async function getStreamUrl(cmd, contentType) {
    if (!cmd) return;
    
    const proxy = document.getElementById('player-proxy').value.trim();
    const res = await fetch('/api/player/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            url: playerState.url,
            mac: playerState.mac,
            token: playerState.token,
            portal_type: playerState.portal_type,
            cmd,
            content_type: contentType === 'vod' ? 'vod' : 'live',
            proxy
        })
    });
    const data = await res.json();
    
    if (data.success) {
        document.getElementById('stream-url').value = data.stream_url;
    } else {
        alert('Failed to get stream URL: ' + data.error);
    }
}

document.getElementById('btn-copy-url').addEventListener('click', () => {
    const url = document.getElementById('stream-url').value;
    if (url) {
        navigator.clipboard.writeText(url);
        document.getElementById('btn-copy-url').textContent = 'Copied!';
        setTimeout(() => document.getElementById('btn-copy-url').textContent = 'Copy', 2000);
    }
});

// ============== PROXIES TAB ==============

let proxyInterval = null;

document.getElementById('btn-fetch-proxies').addEventListener('click', async () => {
    await fetch('/api/proxies/fetch', { method: 'POST' });
    startProxyPolling();
});

document.getElementById('btn-test-proxies').addEventListener('click', async () => {
    await fetch('/api/proxies/test', { method: 'POST' });
    startProxyPolling();
});

document.getElementById('btn-clear-proxies').addEventListener('click', async () => {
    await fetch('/api/proxies', { method: 'DELETE' });
    document.getElementById('proxy-list').value = '';
    document.getElementById('proxy-count').textContent = '0';
});

document.getElementById('btn-save-proxies').addEventListener('click', async () => {
    const proxies = document.getElementById('proxy-list').value;
    await fetch('/api/proxies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ proxies })
    });
    alert('Proxies saved!');
});

function startProxyPolling() {
    if (proxyInterval) clearInterval(proxyInterval);
    proxyInterval = setInterval(updateProxyStatus, 1000);
}

async function updateProxyStatus() {
    const res = await fetch('/api/proxies/status');
    const data = await res.json();
    
    const logBox = document.getElementById('proxy-log');
    logBox.innerHTML = data.logs.map(l => 
        `<div class="log-entry ${l.level}"><span class="time">${l.time}</span>${l.message}</div>`
    ).join('');
    logBox.scrollTop = logBox.scrollHeight;
    
    if (data.proxies.length > 0) {
        document.getElementById('proxy-list').value = data.proxies.join('\n');
        document.getElementById('proxy-count').textContent = data.proxies.length;
    }
    
    if (!data.fetching && !data.testing) {
        clearInterval(proxyInterval);
        proxyInterval = null;
    }
}

// Load proxies on page load
(async () => {
    const res = await fetch('/api/proxies');
    const data = await res.json();
    if (data.proxies.length > 0) {
        document.getElementById('proxy-list').value = data.proxies.join('\n');
        document.getElementById('proxy-count').textContent = data.proxies.length;
    }
})();

// ============== FOUND MACS TAB ==============

document.getElementById('btn-export-txt').addEventListener('click', () => {
    window.location.href = '/api/found/export?format=txt';
});

document.getElementById('btn-export-json').addEventListener('click', () => {
    window.location.href = '/api/found/export?format=json';
});

document.getElementById('btn-clear-found').addEventListener('click', async () => {
    if (confirm('Clear all found MACs?')) {
        await fetch('/api/found', { method: 'DELETE' });
        loadFoundMacs();
    }
});

async function loadFoundMacs() {
    const res = await fetch('/api/found');
    const data = await res.json();
    
    const tbody = document.getElementById('found-tbody');
    tbody.innerHTML = data.map(m => `
        <tr>
            <td>${m.mac}</td>
            <td>${m.expiry || 'N/A'}</td>
            <td>${m.portal || 'N/A'}</td>
            <td>${m.found_at ? new Date(m.found_at).toLocaleString() : 'N/A'}</td>
        </tr>
    `).join('');
}

// Load found MACs on page load
loadFoundMacs();

// ============== SETTINGS TAB ==============

document.getElementById('btn-save-settings').addEventListener('click', async () => {
    const settings = {
        speed: parseInt(document.getElementById('setting-speed').value),
        timeout: parseInt(document.getElementById('setting-timeout').value),
        mac_prefix: document.getElementById('setting-prefix').value,
        use_proxies: document.getElementById('setting-use-proxies').checked,
        auto_save: document.getElementById('setting-auto-save').checked
    };
    
    await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
    });
    
    alert('Settings saved!');
});

// Load settings on page load
(async () => {
    const res = await fetch('/api/settings');
    const settings = await res.json();
    
    document.getElementById('setting-speed').value = settings.speed || 10;
    document.getElementById('setting-timeout').value = settings.timeout || 10;
    document.getElementById('setting-prefix').value = settings.mac_prefix || '00:1A:79:';
    document.getElementById('setting-use-proxies').checked = settings.use_proxies || false;
    document.getElementById('setting-auto-save').checked = settings.auto_save !== false;
})();
