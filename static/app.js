// MacAttack-Web Frontend JavaScript v1.2

let playerState = {
    url: '', mac: '', token: '', token_random: '', portal_type: '',
    categories: { live: [], vod: [], series: [] },
    currentCategory: 'live'
};

let selectedAttackId = null;
let attackInterval = null;

// Tab Navigation
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(btn.dataset.tab).classList.add('active');
        
        if (btn.dataset.tab === 'portals') loadPortals();
        if (btn.dataset.tab === 'maclist') loadMacList();
        if (btn.dataset.tab === 'found') loadFoundMacs();
        if (btn.dataset.tab === 'proxies') { loadProxySources(); loadProxyList(); }
    });
});

// ============== MULTI-PORTAL ATTACK ==============

async function loadPortalSelect() {
    const res = await fetch('/api/portals');
    const portals = await res.json();
    const select = document.getElementById('attack-portal-select');
    select.innerHTML = '<option value="">-- Select Portal --</option>';
    portals.forEach(p => {
        if (p.enabled) {
            select.innerHTML += `<option value="${p.url}">${p.name}</option>`;
        }
    });
}

async function loadMacListCount() {
    const res = await fetch('/api/maclist');
    const data = await res.json();
    document.getElementById('mac-list-count').textContent = data.count;
}

async function loadFoundMacCount() {
    const res = await fetch('/api/found');
    const data = await res.json();
    document.getElementById('found-mac-count').textContent = data.length;
}

document.getElementById('attack-portal-select').addEventListener('change', (e) => {
    if (e.target.value) document.getElementById('attack-url').value = e.target.value;
});

// Start single attack
document.getElementById('btn-start').addEventListener('click', async () => {
    const url = document.getElementById('attack-url').value.trim();
    const mode = document.querySelector('input[name="attack-mode"]:checked').value;
    
    if (!url) { alert('Please enter a portal URL'); return; }
    
    // Check if refresh mode - get MACs for this portal
    if (mode === 'refresh') {
        const foundRes = await fetch('/api/found');
        const foundMacs = await foundRes.json();
        const portalMacs = foundMacs.filter(m => {
            // Match portal URL (normalize both)
            const macPortal = (m.portal || '').replace(/\/+$/, '').toLowerCase();
            const targetPortal = url.replace(/\/+$/, '').toLowerCase();
            return macPortal === targetPortal || macPortal.includes(targetPortal) || targetPortal.includes(macPortal);
        });
        
        if (portalMacs.length === 0) {
            alert('No found MACs for this portal. Use a different mode or select a portal with found MACs.');
            return;
        }
    }
    
    // Check if MAC list mode but no MACs
    if (mode === 'list') {
        const macRes = await fetch('/api/maclist');
        const macData = await macRes.json();
        if (macData.count === 0) {
            alert('MAC List is empty! Please add MACs in the MAC List tab or use Random mode.');
            return;
        }
    }
    
    const res = await fetch('/api/attack/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, mode })
    });
    const data = await res.json();
    
    if (data.success) {
        startStatusPolling();
    } else {
        alert(data.error);
    }
});

// Start multi-portal attack
document.getElementById('btn-start-multi').addEventListener('click', async () => {
    const res = await fetch('/api/portals');
    const portals = await res.json();
    const enabledUrls = portals.filter(p => p.enabled).map(p => p.url);
    
    if (enabledUrls.length === 0) {
        alert('No enabled portals. Add portals in the Portals tab.');
        return;
    }
    
    const mode = document.querySelector('input[name="attack-mode"]:checked').value;
    
    // Check if MAC list mode but no MACs
    if (mode === 'list') {
        const macRes = await fetch('/api/maclist');
        const macData = await macRes.json();
        if (macData.count === 0) {
            alert('MAC List is empty! Please add MACs in the MAC List tab or use Random mode.');
            return;
        }
    }
    
    const startRes = await fetch('/api/attack/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ urls: enabledUrls, mode })
    });
    const data = await startRes.json();
    
    if (data.success) {
        startStatusPolling();
    } else {
        alert(data.error);
    }
});

// Stop all attacks
document.getElementById('btn-stop-all').addEventListener('click', async () => {
    await fetch('/api/attack/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
});

// Clear finished
document.getElementById('btn-clear-finished').addEventListener('click', async () => {
    await fetch('/api/attack/clear', { method: 'POST' });
    selectedAttackId = null;
    document.getElementById('attack-details').style.display = 'none';
});

function startStatusPolling() {
    if (attackInterval) clearInterval(attackInterval);
    attackInterval = setInterval(updateAllAttacks, 300);  // Poll every 300ms for faster updates
}

async function updateAllAttacks() {
    const res = await fetch('/api/attack/status');
    const data = await res.json();
    
    const listDiv = document.getElementById('attacks-list');
    
    if (!data.attacks || data.attacks.length === 0) {
        listDiv.innerHTML = '<div class="no-attacks">No active attacks</div>';
        document.getElementById('attack-details').style.display = 'none';
        return;
    }
    
    // Auto-select first attack if none selected
    if (!selectedAttackId && data.attacks.length > 0) {
        selectedAttackId = data.attacks[0].id;
    }
    
    listDiv.innerHTML = data.attacks.map(a => {
        // Build progress info for list/refresh modes
        let progressInfo = '';
        if (a.mode === 'list' || a.mode === 'refresh') {
            const total = a.mac_list_total || 0;
            const current = a.mac_list_index || a.tested || 0;
            if (total > 0) {
                const percent = Math.round((current / total) * 100);
                progressInfo = ` | 📋 ${current}/${total} (${percent}%)`;
            }
        }
        
        return `
        <div class="attack-item ${a.id === selectedAttackId ? 'selected' : ''} ${a.running ? '' : 'finished'}" data-id="${a.id}">
            <div class="attack-info">
                <span class="attack-url">${a.portal_url}</span>
                <span class="attack-stats">Tested: ${a.tested} | Hits: ${a.hits}${progressInfo} | ${a.running ? '🟢 Running' : '⚫ Stopped'}</span>
            </div>
            <div class="attack-actions">
                ${a.running ? `<button class="btn btn-small btn-warning btn-pause-attack" data-id="${a.id}">${a.paused ? '▶' : '⏸'}</button>` : ''}
                ${a.running ? `<button class="btn btn-small btn-danger btn-stop-attack" data-id="${a.id}">⏹</button>` : ''}
            </div>
        </div>
    `}).join('');
    
    // Click handlers
    listDiv.querySelectorAll('.attack-item').forEach(item => {
        item.addEventListener('click', (e) => {
            if (!e.target.classList.contains('btn')) {
                selectedAttackId = item.dataset.id;
                updateSelectedAttack(data.attacks.find(a => a.id === selectedAttackId));
            }
        });
    });
    
    listDiv.querySelectorAll('.btn-pause-attack').forEach(btn => {
        btn.addEventListener('click', async () => {
            await fetch('/api/attack/pause', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: btn.dataset.id })
            });
        });
    });
    
    listDiv.querySelectorAll('.btn-stop-attack').forEach(btn => {
        btn.addEventListener('click', async () => {
            await fetch('/api/attack/stop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: btn.dataset.id })
            });
        });
    });
    
    // Update selected attack details
    if (selectedAttackId) {
        const selected = data.attacks.find(a => a.id === selectedAttackId);
        if (selected) updateSelectedAttack(selected);
    }
}

function updateSelectedAttack(attack) {
    if (!attack) return;
    
    document.getElementById('attack-details').style.display = 'block';
    document.getElementById('stat-tested').textContent = attack.tested;
    document.getElementById('stat-hits').textContent = attack.hits;
    document.getElementById('stat-errors').textContent = attack.errors;
    document.getElementById('current-portal').textContent = attack.portal_url;
    document.getElementById('current-mac').textContent = attack.current_mac || '-';
    document.getElementById('current-proxy').textContent = attack.current_proxy || 'Direct (no proxy)';
    
    const mins = Math.floor(attack.elapsed / 60);
    const secs = attack.elapsed % 60;
    document.getElementById('stat-time').textContent = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    
    const foundList = document.getElementById('found-list');
    foundList.innerHTML = (attack.found_macs || []).map(m => 
        `<div class="log-entry success"><strong>${m.mac}</strong> - ${m.expiry} - ${m.channels || 0} channels</div>`
    ).join('');
    if (attack.found_macs && attack.found_macs.length > 0) {
        foundList.scrollTop = foundList.scrollHeight;
    }
    
    const logBox = document.getElementById('attack-log');
    logBox.innerHTML = (attack.logs || []).map(l => 
        `<div class="log-entry ${l.level}"><span class="time">[${l.time}]</span> ${l.message}</div>`
    ).join('');
    logBox.scrollTop = logBox.scrollHeight;
}

loadPortalSelect();
loadMacListCount();
loadFoundMacCount();
startStatusPolling();


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
    
    if (!url || !mac) { alert('Please enter URL and MAC address'); return; }
    
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
            playerState = { ...playerState, url, mac, token: data.token, token_random: data.token_random, portal_type: data.portal_type };
            playerState.categories = { live: data.live, vod: data.vod, series: data.series };
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
    list.innerHTML = cats.map(c => `<div class="list-item" data-id="${c.id}" data-type="${playerState.currentCategory}">${c.name}</div>`).join('');
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
            url: playerState.url, mac: playerState.mac, token: playerState.token,
            token_random: playerState.token_random, portal_type: playerState.portal_type,
            category_type: typeMap[categoryType], category_id: categoryId, proxy
        })
    });
    const data = await res.json();
    
    if (data.success) {
        const list = document.getElementById('channel-list');
        list.innerHTML = data.channels.map(ch => `<div class="list-item" data-cmd="${ch.cmd || ''}" data-type="${categoryType}">${ch.name}</div>`).join('');
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
            url: playerState.url, mac: playerState.mac, token: playerState.token,
            token_random: playerState.token_random, portal_type: playerState.portal_type,
            cmd, content_type: contentType === 'vod' ? 'vod' : 'live', proxy
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

// Video Player
let hls = null;

document.getElementById('btn-play').addEventListener('click', () => {
    const url = document.getElementById('stream-url').value;
    if (!url) { alert('No stream URL'); return; }
    playStream(url);
});

document.getElementById('btn-close-player').addEventListener('click', () => {
    stopPlayer();
    document.getElementById('video-panel').style.display = 'none';
});

document.getElementById('btn-open-external').addEventListener('click', () => {
    const url = document.getElementById('stream-url').value;
    if (!url) { alert('No stream URL'); return; }
    
    // Try multiple methods to open in VLC
    // Method 1: vlc:// protocol (works if VLC is registered)
    const vlcUrl = `vlc://${url}`;
    
    // Method 2: Create a temporary .m3u file download
    const m3uContent = `#EXTM3U\n#EXTINF:-1,Stream\n${url}`;
    const blob = new Blob([m3uContent], { type: 'audio/x-mpegurl' });
    const downloadUrl = URL.createObjectURL(blob);
    
    // Show options
    const choice = confirm('Click OK to download .m3u playlist file (open with VLC)\nClick Cancel to try vlc:// protocol directly');
    
    if (choice) {
        // Download m3u file
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = 'stream.m3u';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(downloadUrl);
    } else {
        // Try vlc:// protocol
        window.location.href = vlcUrl;
    }
});

function playStream(url) {
    const video = document.getElementById('video-player');
    const panel = document.getElementById('video-panel');
    const status = document.getElementById('player-status');
    stopPlayer();
    panel.style.display = 'block';
    status.textContent = 'Loading stream...';
    
    // Log the URL for debugging
    console.log('Attempting to play:', url);
    
    // Check if HLS stream (.m3u8)
    if (url.includes('.m3u8') || url.includes('m3u8')) {
        playHLS(video, url, status);
    } 
    // MPEG-TS streams - try HLS.js first, then direct
    else if (url.includes('.ts') || url.includes('/live/') || url.includes('/ch/')) {
        // Most IPTV streams are MPEG-TS which browsers can't play natively
        // Try direct play first
        video.src = url;
        
        video.oncanplay = () => {
            status.textContent = 'Playing';
            video.play().catch(() => status.textContent = 'Click video to play');
        };
        
        video.onerror = () => {
            console.log('Direct play failed, stream format not supported');
            status.innerHTML = `
                <span style="color: #ff6b6b;">⚠️ MPEG-TS format - Browser cannot play this stream</span><br>
                <small>Use "Open in VLC" button or copy URL to VLC/media player</small>
            `;
        };
        
        // Set timeout for loading
        setTimeout(() => {
            if (video.readyState < 2) {
                status.innerHTML = `
                    <span style="color: #ffa500;">⏳ Stream loading slowly or format not supported</span><br>
                    <small>Try "Open in VLC" for better compatibility</small>
                `;
            }
        }, 5000);
    }
    // Standard video formats (mp4, webm, etc.)
    else {
        video.src = url;
        
        video.oncanplay = () => { 
            status.textContent = 'Playing'; 
            video.play().catch(() => status.textContent = 'Click video to play'); 
        };
        
        video.onerror = (e) => { 
            console.error('Video error:', e);
            status.innerHTML = `
                <span style="color: #ff6b6b;">❌ Cannot play this format in browser</span><br>
                <small>Use "Open in VLC" button</small>
            `;
        };
    }
    
    // Click to play (for autoplay restrictions)
    video.onclick = () => video.play().catch(() => {});
}

function playHLS(video, url, status) {
    if (Hls.isSupported()) {
        hls = new Hls({ 
            debug: false, 
            enableWorker: true,
            lowLatencyMode: true,
            backBufferLength: 90
        });
        hls.loadSource(url);
        hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, () => { 
            status.textContent = 'Playing (HLS)'; 
            video.play().catch(() => status.textContent = 'Click to play'); 
        });
        hls.on(Hls.Events.ERROR, (e, d) => { 
            if (d.fatal) {
                status.innerHTML = `
                    <span style="color: #ff6b6b;">HLS Error: ${d.type}</span><br>
                    <small>Try "Open in VLC"</small>
                `;
                console.error('HLS Error:', d);
            }
        });
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        // Safari native HLS
        video.src = url;
        video.onloadedmetadata = () => { 
            status.textContent = 'Playing (Native HLS)'; 
            video.play().catch(() => status.textContent = 'Click to play'); 
        };
    } else {
        status.innerHTML = `
            <span style="color: #ff6b6b;">HLS not supported</span><br>
            <small>Use "Open in VLC"</small>
        `;
    }
}

function stopPlayer() {
    const video = document.getElementById('video-player');
    if (hls) { hls.destroy(); hls = null; }
    video.pause(); 
    video.src = ''; 
    video.load();
    video.oncanplay = null;
    video.onerror = null;
    video.onclick = null;
}


// ============== PORTALS TAB ==============

async function loadPortals() {
    const res = await fetch('/api/portals');
    const portals = await res.json();
    const tbody = document.getElementById('portals-tbody');
    tbody.innerHTML = portals.map(p => `
        <tr>
            <td>${p.name}</td>
            <td>${p.url}</td>
            <td><span class="status-badge ${p.enabled ? 'enabled' : 'disabled'}">${p.enabled ? 'Enabled' : 'Disabled'}</span></td>
            <td>
                <button class="btn btn-small btn-secondary btn-toggle" data-id="${p.id}" data-enabled="${p.enabled}">${p.enabled ? 'Disable' : 'Enable'}</button>
                <button class="btn btn-small btn-danger btn-delete" data-id="${p.id}">Delete</button>
            </td>
        </tr>
    `).join('');
    
    tbody.querySelectorAll('.btn-toggle').forEach(btn => {
        btn.addEventListener('click', async () => {
            await fetch(`/api/portals/${btn.dataset.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: btn.dataset.enabled !== 'true' })
            });
            loadPortals(); loadPortalSelect();
        });
    });
    
    tbody.querySelectorAll('.btn-delete').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (confirm('Delete?')) {
                await fetch(`/api/portals/${btn.dataset.id}`, { method: 'DELETE' });
                loadPortals(); loadPortalSelect();
            }
        });
    });
}

document.getElementById('btn-add-portal').addEventListener('click', async () => {
    const name = document.getElementById('portal-name').value.trim();
    const url = document.getElementById('portal-url').value.trim();
    if (!url) { alert('Enter URL'); return; }
    await fetch('/api/portals', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, url }) });
    document.getElementById('portal-name').value = '';
    document.getElementById('portal-url').value = '';
    loadPortals(); loadPortalSelect();
});

// ============== MAC LIST TAB ==============

async function loadMacList() {
    const res = await fetch('/api/maclist');
    const data = await res.json();
    
    // For large lists (>10000), don't show all MACs in textarea to avoid browser freeze
    if (data.count > 10000) {
        document.getElementById('mac-list-textarea').value = 
            `# ${data.count} MACs loaded (too many to display)\n` +
            `# First 100 MACs:\n` +
            data.macs.slice(0, 100).join('\n') +
            `\n\n# ... and ${data.count - 100} more`;
    } else {
        document.getElementById('mac-list-textarea').value = data.macs.join('\n');
    }
    
    document.getElementById('maclist-count').textContent = data.count;
    document.getElementById('mac-list-count').textContent = data.count;
}

document.getElementById('btn-save-maclist').addEventListener('click', async () => {
    const macs = document.getElementById('mac-list-textarea').value;
    const res = await fetch('/api/maclist', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ macs }) });
    const data = await res.json();
    if (data.success) {
        document.getElementById('maclist-count').textContent = data.count;
        document.getElementById('mac-list-count').textContent = data.count;
        alert(`Saved ${data.count} MACs`);
    }
});

document.getElementById('btn-clear-maclist').addEventListener('click', async () => {
    if (confirm('Clear all?')) {
        await fetch('/api/maclist', { method: 'DELETE' });
        document.getElementById('mac-list-textarea').value = '';
        document.getElementById('maclist-count').textContent = '0';
        document.getElementById('mac-list-count').textContent = '0';
    }
});

// Show file info when selected
document.getElementById('mac-file-input').addEventListener('change', (e) => {
    const file = e.target.files[0];
    const fileInfo = document.getElementById('file-info');
    if (file) {
        const sizeMB = (file.size / (1024 * 1024)).toFixed(2);
        fileInfo.textContent = `(${file.name} - ${sizeMB} MB)`;
    } else {
        fileInfo.textContent = '';
    }
});

document.getElementById('btn-import-file').addEventListener('click', async () => {
    const fileInput = document.getElementById('mac-file-input');
    if (!fileInput.files.length) { alert('Select file'); return; }
    
    const file = fileInput.files[0];
    const sizeMB = (file.size / (1024 * 1024)).toFixed(2);
    
    // Show progress
    const progressDiv = document.getElementById('import-progress');
    const progressBar = document.getElementById('import-progress-bar');
    const statusDiv = document.getElementById('import-status');
    const btn = document.getElementById('btn-import-file');
    
    progressDiv.style.display = 'block';
    progressBar.style.width = '0%';
    statusDiv.textContent = `Uploading ${file.name} (${sizeMB} MB)...`;
    btn.disabled = true;
    btn.textContent = 'Importing...';
    
    try {
        const formData = new FormData();
        formData.append('file', file);
        
        // Use XMLHttpRequest for progress tracking
        const xhr = new XMLHttpRequest();
        
        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                progressBar.style.width = percent + '%';
                statusDiv.textContent = `Uploading: ${percent}%`;
            }
        });
        
        const response = await new Promise((resolve, reject) => {
            xhr.onload = () => {
                if (xhr.status === 200) {
                    resolve(JSON.parse(xhr.responseText));
                } else {
                    reject(new Error('Upload failed'));
                }
            };
            xhr.onerror = () => reject(new Error('Upload failed'));
            xhr.open('POST', '/api/maclist/import');
            xhr.send(formData);
            
            statusDiv.textContent = 'Processing MACs...';
            progressBar.style.width = '100%';
        });
        
        if (response.success) {
            loadMacList();
            loadFoundMacCount();
            let msg = `✅ Imported ${response.count} MACs`;
            if (response.duplicates > 0) msg += `\n⚠️ ${response.duplicates} duplicates removed`;
            if (response.invalid > 0) msg += `\n❌ ${response.invalid} invalid lines skipped`;
            alert(msg);
        } else {
            alert('Error: ' + (response.error || 'Import failed'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Import from File';
        setTimeout(() => {
            progressDiv.style.display = 'none';
        }, 2000);
    }
});

// ============== PROXIES TAB ==============

let proxyInterval = null;

async function loadProxySources() {
    const res = await fetch('/api/proxies/sources');
    const data = await res.json();
    document.getElementById('proxy-sources').value = (data.sources || []).join('\n');
}

document.getElementById('btn-save-sources').addEventListener('click', async () => {
    const sources = document.getElementById('proxy-sources').value;
    await fetch('/api/proxies/sources', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sources }) });
    alert('Sources saved!');
});

document.getElementById('btn-fetch-proxies').addEventListener('click', async () => {
    await fetch('/api/proxies/fetch', { method: 'POST' });
    startProxyPolling();
});

document.getElementById('btn-test-proxies').addEventListener('click', async () => {
    await fetch('/api/proxies/test', { method: 'POST' });
    startProxyPolling();
});

document.getElementById('btn-test-autodetect').addEventListener('click', async () => {
    await fetch('/api/proxies/test-autodetect', { method: 'POST' });
    startProxyPolling();
});

document.getElementById('btn-remove-failed').addEventListener('click', async () => {
    const res = await fetch('/api/proxies/remove-failed', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
        alert(`Removed ${data.removed} failed proxies. ${data.remaining} remaining.`);
        loadProxyList();
    } else {
        alert(data.error || 'No failed proxies to remove');
    }
});

document.getElementById('btn-reset-errors').addEventListener('click', async () => {
    await fetch('/api/proxies/reset-errors', { method: 'POST' });
    alert('Proxy errors reset');
});

document.getElementById('btn-clear-proxies').addEventListener('click', async () => {
    await fetch('/api/proxies', { method: 'DELETE' });
    document.getElementById('proxy-list').value = '';
    document.getElementById('proxy-count').textContent = '0';
});

document.getElementById('btn-save-proxies').addEventListener('click', async () => {
    const proxies = document.getElementById('proxy-list').value;
    const res = await fetch('/api/proxies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ proxies }) });
    const data = await res.json();
    if (data.success) {
        document.getElementById('proxy-count').textContent = data.count || 0;
        alert('Proxies saved!');
    }
});

// Load proxy list from server
async function loadProxyList() {
    const res = await fetch('/api/proxies');
    const data = await res.json();
    if (data.proxies && data.proxies.length > 0) {
        document.getElementById('proxy-list').value = data.proxies.join('\n');
        document.getElementById('proxy-count').textContent = data.proxies.length;
    } else {
        document.getElementById('proxy-list').value = '';
        document.getElementById('proxy-count').textContent = '0';
    }
}

// Import proxies with auto-prefix
document.getElementById('btn-import-proxies').addEventListener('click', async () => {
    const importText = document.getElementById('proxy-import-textarea').value.trim();
    if (!importText) { alert('Paste proxies to import'); return; }
    
    const proxyType = document.getElementById('proxy-import-type').value;
    const lines = importText.split('\n').map(l => l.trim()).filter(l => l);
    
    const processedProxies = lines.map(proxy => {
        // If already has a prefix, keep it
        if (proxy.startsWith('socks4://') || proxy.startsWith('socks5://') || proxy.startsWith('http://')) {
            return proxy;
        }
        // Apply selected type prefix
        if (proxyType === 'socks4') {
            return `socks4://${proxy}`;
        } else if (proxyType === 'socks5') {
            return `socks5://${proxy}`;
        }
        // HTTP = no prefix needed (default)
        return proxy;
    });
    
    // Get existing proxies and merge
    const existingText = document.getElementById('proxy-list').value.trim();
    const existingProxies = existingText ? existingText.split('\n').map(l => l.trim()).filter(l => l) : [];
    
    // Combine and remove duplicates
    const allProxies = [...new Set([...existingProxies, ...processedProxies])];
    
    // Update textarea
    document.getElementById('proxy-list').value = allProxies.join('\n');
    document.getElementById('proxy-count').textContent = allProxies.length;
    
    // Clear import textarea
    document.getElementById('proxy-import-textarea').value = '';
    
    // Save to backend
    await fetch('/api/proxies', { 
        method: 'POST', 
        headers: { 'Content-Type': 'application/json' }, 
        body: JSON.stringify({ proxies: allProxies.join('\n') }) 
    });
    
    alert(`Imported ${processedProxies.length} proxies (${proxyType.toUpperCase()}). Total: ${allProxies.length}`);
});

function startProxyPolling() {
    if (proxyInterval) clearInterval(proxyInterval);
    proxyInterval = setInterval(updateProxyStatus, 1000);
}

async function updateProxyStatus() {
    const res = await fetch('/api/proxies/status');
    const data = await res.json();
    const logBox = document.getElementById('proxy-log');
    logBox.innerHTML = data.logs.map(l => `<div class="log-entry ${l.level}"><span class="time">${l.time}</span>${l.message}</div>`).join('');
    logBox.scrollTop = logBox.scrollHeight;
    if (data.proxies.length > 0) {
        document.getElementById('proxy-list').value = data.proxies.join('\n');
        document.getElementById('proxy-count').textContent = data.proxies.length;
    }
    if (!data.fetching && !data.testing) { clearInterval(proxyInterval); proxyInterval = null; }
}

(async () => {
    const res = await fetch('/api/proxies');
    const data = await res.json();
    if (data.proxies.length > 0) {
        document.getElementById('proxy-list').value = data.proxies.join('\n');
        document.getElementById('proxy-count').textContent = data.proxies.length;
    }
})();

// ============== FOUND MACS TAB ==============

document.getElementById('btn-export-txt').addEventListener('click', () => { window.location.href = '/api/found/export?format=txt'; });
document.getElementById('btn-export-json').addEventListener('click', () => { window.location.href = '/api/found/export?format=json'; });
document.getElementById('btn-clear-found').addEventListener('click', async () => {
    if (confirm('Clear all?')) { await fetch('/api/found', { method: 'DELETE' }); loadFoundMacs(); }
});

async function loadFoundMacs() {
    const res = await fetch('/api/found');
    const data = await res.json();
    document.getElementById('found-tbody').innerHTML = data.map(m => {
        // Check for DE channels
        const hasDE = m.has_de || (m.genres || []).some(g => 
            g.toUpperCase().startsWith('DE') || 
            g.toUpperCase().includes('GERMAN') || 
            g.toUpperCase().includes('DEUTSCH')
        );
        const deIcon = hasDE ? '✅' : '❌';
        const deTitle = hasDE ? (m.de_genres || []).join(', ') || 'Has DE channels' : 'No DE channels';
        
        return `
        <tr>
            <td>${m.mac}</td>
            <td>${m.expiry || 'N/A'}</td>
            <td>${m.channels || 0}</td>
            <td title="${deTitle}" style="text-align:center;font-size:1.2em">${deIcon}</td>
            <td>${m.portal || 'N/A'}</td>
            <td title="${(m.genres || []).join(', ')}">${(m.genres || []).length} genres</td>
            <td>${m.found_at ? new Date(m.found_at).toLocaleString() : 'N/A'}</td>
            <td>
                <button class="btn btn-small btn-secondary btn-details" data-mac='${JSON.stringify(m).replace(/'/g, "&#39;")}'>Details</button>
            </td>
        </tr>
    `}).join('');
    
    // Add click handlers for details buttons
    document.querySelectorAll('.btn-details').forEach(btn => {
        btn.addEventListener('click', () => {
            const mac = JSON.parse(btn.dataset.mac);
            showMacDetails(mac);
        });
    });
}

function showMacDetails(mac) {
    let details = `MAC: ${mac.mac}\n`;
    details += `Portal: ${mac.portal || 'N/A'}\n`;
    details += `Expiry: ${mac.expiry || 'N/A'}\n`;
    details += `Channels: ${mac.channels || 0}\n`;
    
    // DE Channels info
    const hasDE = mac.has_de || (mac.genres || []).some(g => 
        g.toUpperCase().startsWith('DE') || 
        g.toUpperCase().includes('GERMAN') || 
        g.toUpperCase().includes('DEUTSCH')
    );
    details += `DE Channels: ${hasDE ? 'Yes ✅' : 'No ❌'}\n`;
    if (mac.de_genres && mac.de_genres.length > 0) {
        details += `DE Genres: ${mac.de_genres.join(', ')}\n`;
    }
    
    if (mac.username && mac.password) {
        details += `\nCredentials:\n`;
        details += `Username: ${mac.username}\n`;
        details += `Password: ${mac.password}\n`;
    }
    
    if (mac.backend_url) {
        details += `Backend: ${mac.backend_url}\n`;
    }
    
    if (mac.max_connections) {
        details += `Max Connections: ${mac.max_connections}\n`;
    }
    
    if (mac.created_at) {
        details += `Created: ${mac.created_at}\n`;
    }
    
    if (mac.genres && mac.genres.length > 0) {
        details += `\nGenres (${mac.genres.length}):\n${mac.genres.join(', ')}\n`;
    }
    
    if (mac.vod_categories && mac.vod_categories.length > 0) {
        details += `\nVOD Categories (${mac.vod_categories.length}):\n${mac.vod_categories.join(', ')}\n`;
    }
    
    if (mac.series_categories && mac.series_categories.length > 0) {
        details += `\nSeries Categories (${mac.series_categories.length}):\n${mac.series_categories.join(', ')}\n`;
    }
    
    alert(details);
}
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
    await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings) });
    alert('Settings saved!');
});

document.getElementById('btn-save-proxy-settings').addEventListener('click', async () => {
    const settings = {
        proxy_test_threads: parseInt(document.getElementById('setting-proxy-test-threads').value),
        max_proxy_errors: parseInt(document.getElementById('setting-max-proxy-errors').value)
    };
    await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings) });
    alert('Proxy settings saved!');
});

(async () => {
    const res = await fetch('/api/settings');
    const s = await res.json();
    document.getElementById('setting-speed').value = s.speed || 10;
    document.getElementById('setting-timeout').value = s.timeout || 10;
    document.getElementById('setting-prefix').value = s.mac_prefix || '00:1A:79:';
    document.getElementById('setting-use-proxies').checked = s.use_proxies || false;
    document.getElementById('setting-auto-save').checked = s.auto_save !== false;
    document.getElementById('setting-proxy-test-threads').value = s.proxy_test_threads || 50;
    document.getElementById('setting-max-proxy-errors').value = s.max_proxy_errors || 5;
})();

// ============== AUTHENTICATION ==============

async function loadAuthStatus() {
    try {
        const res = await fetch('/api/auth/status');
        const data = await res.json();
        const statusEl = document.getElementById('auth-status');
        
        if (data.enabled) {
            statusEl.innerHTML = '<div class="help-text" style="color: #28a745;">✅ Authentication is enabled</div>';
        } else {
            statusEl.innerHTML = '<div class="help-text" style="color: #ffc107;">⚠️ Authentication is disabled - anyone can access this interface</div>';
        }
    } catch (e) {
        console.error('Error loading auth status:', e);
    }
}

document.getElementById('btn-update-auth').addEventListener('click', async () => {
    const username = document.getElementById('auth-username').value.trim();
    const password = document.getElementById('auth-password').value;
    
    if (!username || !password) {
        alert('Please enter both username and password');
        return;
    }
    
    if (password.length < 4) {
        alert('Password must be at least 4 characters');
        return;
    }
    
    try {
        const res = await fetch('/api/auth/change', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'change', username, password })
        });
        const data = await res.json();
        
        if (data.success) {
            alert('Credentials updated! You may need to log in again.');
            document.getElementById('auth-username').value = '';
            document.getElementById('auth-password').value = '';
            loadAuthStatus();
        } else {
            alert('Error: ' + data.error);
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
});

document.getElementById('btn-disable-auth').addEventListener('click', async () => {
    if (!confirm('Are you sure you want to disable authentication? Anyone with access to this URL will be able to use MacAttack-Web.')) {
        return;
    }
    
    try {
        const res = await fetch('/api/auth/change', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'disable' })
        });
        const data = await res.json();
        
        if (data.success) {
            alert('Authentication disabled');
            loadAuthStatus();
        } else {
            alert('Error: ' + data.error);
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
});

loadAuthStatus();
