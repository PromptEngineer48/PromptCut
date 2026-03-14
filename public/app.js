// DOM Elements
const uploadBox = document.getElementById('video-upload');
const uploadOverlay = document.getElementById('upload-overlay');
const splitView = document.getElementById('video-split-view');

const playerOriginal = document.getElementById('player-original');
const playerProcessed = document.getElementById('player-processed');
const emptyProcessed = document.getElementById('player-processed-empty');
const loaderProcessing = document.getElementById('loader-processing');

// Buttons
const btnDetect = document.getElementById('btn-detect');
const btnProcess = document.getElementById('btn-process');
const btnExport = document.getElementById('btn-export');

// Sliders
const sliderThreshold = document.getElementById('param-threshold');
const sliderMinSilence = document.getElementById('param-min-silence');
const sliderPadding = document.getElementById('param-padding');
const toggleEnhanceAudio = document.getElementById('param-enhance-audio');
const btnAutoThreshold = document.getElementById('btn-auto-threshold');

// Slider Values Labels
const valThreshold = document.getElementById('val-threshold');
const valMinSilence = document.getElementById('val-min-silence');
const valPadding = document.getElementById('val-padding');

// State
let selectedFile = null;
let originalDuration = 0;
let finalOutputUrl = null;
let wavesurfer = null;


// ─── 1. EVENT LISTENERS ───

// Update slider labels dynamically
sliderThreshold.addEventListener('input', (e) => valThreshold.innerText = `${e.target.value} dB`);
sliderMinSilence.addEventListener('input', (e) => valMinSilence.innerText = `${e.target.value}s`);
sliderPadding.addEventListener('input', (e) => valPadding.innerText = `${e.target.value}s`);

// Handle File Selection
uploadBox.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;

    selectedFile = file;
    
    // Load local video into original player immediately
    const objectUrl = URL.createObjectURL(file);
    playerOriginal.src = objectUrl;

    // Build the visual waveform synced to the video
    document.getElementById('timeline-placeholder').classList.add('hidden');
    if (wavesurfer) wavesurfer.destroy();
    wavesurfer = WaveSurfer.create({
        container: '#waveform-container',
        media: playerOriginal,
        url: objectUrl,
        waveColor: '#10b981',
        progressColor: '#059669',
        height: 60,
        barWidth: 2,
        barGap: 1,
        barRadius: 2,
    });
    
    // Get duration once metadata loads
    playerOriginal.onloadedmetadata = () => {
        originalDuration = playerOriginal.duration;
        document.getElementById('stat-original').innerText = formatTime(originalDuration);
        document.getElementById('timeline-status').innerText = `Ready to analyze ${file.name}`;
    };

    // Update UI State
    uploadOverlay.classList.add('hidden');
    splitView.classList.remove('hidden');
    
    btnDetect.disabled = false;
    btnProcess.disabled = false;
});

// Auto-run analysis when sliders change
[sliderThreshold, sliderMinSilence, sliderPadding].forEach(slider => {
    slider.addEventListener('change', () => {
        if(selectedFile) runAnalysis();
    });
});

btnDetect.addEventListener('click', runAnalysis);
btnProcess.addEventListener('click', runProcessing);

btnExport.addEventListener('click', () => {
    if(finalOutputUrl) {
        const a = document.createElement('a');
        a.href = finalOutputUrl;
        a.download = `PromptCut_${selectedFile.name}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }
});

btnAutoThreshold.addEventListener('click', async () => {
    if (!selectedFile) return;
    
    btnAutoThreshold.disabled = true;
    const oldText = btnAutoThreshold.innerText;
    btnAutoThreshold.innerText = "Calc...";

    const formData = new FormData();
    formData.append('file', selectedFile);

    try {
        const response = await fetch('/suggest-threshold', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) throw new Error("Server error during suggestion");

        const data = await response.json();
        const suggested = data.suggested_threshold_db;
        
        // Update slider and label
        sliderThreshold.value = suggested;
        valThreshold.innerText = `${suggested} dB`;
        
        // Auto-run analysis with new threshold
        runAnalysis();
    } catch (error) {
        console.error(error);
        alert("Error calculating auto threshold.");
    } finally {
        btnAutoThreshold.disabled = false;
        btnAutoThreshold.innerText = oldText;
    }
});


// ─── 2. API CALLS ───

async function runAnalysis() {
    if (!selectedFile) return;

    btnDetect.disabled = true;
    document.getElementById('timeline-status').innerText = "Analyzing audio... (Generating Server Preview)";
    
    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('threshold_db', sliderThreshold.value);
    formData.append('min_silence_duration', sliderMinSilence.value);
    formData.append('padding', sliderPadding.value);

    try {
        const response = await fetch('/detect', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) throw new Error("Server error during detection");

        const data = await response.json();
        updateStats(data);
        renderTimeline(data);
        
        document.getElementById('timeline-status').innerText = "Analysis complete.";
    } catch (error) {
        console.error(error);
        document.getElementById('timeline-status').innerText = "Error analyzing audio.";
    } finally {
        btnDetect.disabled = false;
    }
}

async function runProcessing() {
    if (!selectedFile) return;

    // UI Updates
    btnDetect.disabled = true;
    btnProcess.disabled = true;
    btnExport.disabled = true;
    emptyProcessed.classList.add('hidden');
    playerProcessed.classList.add('hidden');
    loaderProcessing.classList.remove('hidden');
    
    const loaderText = document.getElementById('loader-text');
    loaderText.innerText = "Uploading to server...";

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('threshold_db', sliderThreshold.value);
    formData.append('min_silence_duration', sliderMinSilence.value);
    formData.append('padding', sliderPadding.value);
    formData.append('enhance_audio', toggleEnhanceAudio.checked);

    try {
        // 1. Submit Job
        const submitResponse = await fetch('/process', {
            method: 'POST',
            body: formData
        });
        
        if (!submitResponse.ok) throw new Error("Failed to submit job");
        const job = await submitResponse.json();
        
        // 2. Poll Status
        pollJobStatus(job.job_id);

    } catch (error) {
        console.error(error);
        loaderProcessing.classList.add('hidden');
        emptyProcessed.classList.remove('hidden');
        alert("Processing failed. Check console for details.");
        
        btnDetect.disabled = false;
        btnProcess.disabled = false;
    }
}

async function pollJobStatus(jobId) {
    const loaderText = document.getElementById('loader-text');
    
    const interval = setInterval(async () => {
        try {
            const res = await fetch(`/jobs/${jobId}`);
            const data = await res.json();

            if (data.status === 'processing' || data.status === 'pending') {
                loaderText.innerText = `Processing via FFmpeg...`;
            } 
            else if (data.status === 'done') {
                clearInterval(interval);
                
                // Job complete, load the final video!
                finalOutputUrl = data.output_url;
                
                loaderProcessing.classList.add('hidden');
                playerProcessed.src = finalOutputUrl;
                playerProcessed.classList.remove('hidden');
                
                btnExport.disabled = false;
                btnDetect.disabled = false;
                btnProcess.disabled = false;
                
                // Also update stats if we didn't run Analysis first
                if (data.stats) {
                    updateStats({
                        duration: data.stats.original_duration,
                        time_removable: data.stats.time_saved,
                        percent_removable: data.stats.percent_removed,
                        silence_count: data.stats.silences_found
                    });
                }
            }
            else if (data.status === 'error') {
                clearInterval(interval);
                throw new Error(data.error || "Unknown server error");
            }
        } catch (error) {
            clearInterval(interval);
            console.error(error);
            loaderProcessing.classList.add('hidden');
            emptyProcessed.classList.remove('hidden');
            alert(`Processing failed: ${error.message}`);
            
            btnDetect.disabled = false;
            btnProcess.disabled = false;
        }
    }, 1000);
}


// ─── 3. UI RENDERING ───

function updateStats(data) {
    const newDuration = Math.max(0, data.duration - data.time_removable);
    
    document.getElementById('stat-original').innerText = formatTime(data.duration);
    document.getElementById('stat-new').innerText = formatTime(newDuration);
    document.getElementById('stat-cuts').innerText = data.silence_count;
    document.getElementById('stat-saved').innerText = `${data.time_removable.toFixed(1)}s (${data.percent_removable.toFixed(0)}%)`;
}

function renderTimeline(data) {
    const totalDuration = data.duration;
    
    const track = document.getElementById('tl-track');
    track.innerHTML = ''; // clear old segments

    // Speech segments
    data.segments_to_keep.forEach(seg => {
        const div = document.createElement('div');
        div.className = 'tl-segment tl-speech';
        div.title = `Keep: ${seg.duration.toFixed(1)}s`;
        
        // Convert time to percentage width
        const leftPct = (seg.start / totalDuration) * 100;
        const widthPct = (seg.duration / totalDuration) * 100;
        
        div.style.left = `${leftPct}%`;
        div.style.width = `${widthPct}%`;
        track.appendChild(div);
    });

    // Silence segments
    data.silence_intervals.forEach(sil => {
        const div = document.createElement('div');
        div.className = 'tl-segment tl-silence';
        div.title = `Remove: ${sil.duration.toFixed(1)}s (${sil.db.toFixed(1)}dB)`;
        
        const leftPct = (sil.start / totalDuration) * 100;
        const widthPct = (sil.duration / totalDuration) * 100;
        
        div.style.left = `${leftPct}%`;
        div.style.width = `${widthPct}%`;
        track.appendChild(div);
    });
}

function formatTime(seconds) {
    if (!seconds || isNaN(seconds)) return "00:00.0";
    const m = Math.floor(seconds / 60).toString().padStart(2, '0');
    const s = Math.floor(seconds % 60).toString().padStart(2, '0');
    const ms = Math.floor((seconds % 1) * 10).toString();
    return `${m}:${s}.${ms}`;
}
