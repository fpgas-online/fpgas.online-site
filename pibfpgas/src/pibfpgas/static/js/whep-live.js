"use strict";

/*
 * WHEP live view with HLS rewind fallback.
 *
 * For every <div class="whep-live" data-whep-url="/cam/<host>/whep"
 * data-hls-id="<video element id>"> this builds a bare <video> fed by
 * MediaMTXWebRTCReader (mediamtx-reader.js) and swaps it with the existing
 * video.js HLS player:
 *
 *  - live (default): WHEP video visible (~0.3 s behind the camera), HLS
 *    player hidden and paused. A "rewind" button swaps to...
 *  - rewind: the video.js liveui player with its seekable DVR window
 *    (~5 s behind live). A "live" button swaps back.
 *
 * If WHEP never delivers a track (mediamtx not deployed, UDP+TCP media
 * port unreachable, codec unsupported) the HLS player simply stays -- the
 * reader keeps retrying in the background and promotes the live view when
 * it succeeds, unless the user explicitly chose rewind.
 *
 * video.js must NOT front the WHEP MediaStream: its srcObject handling is
 * broken, hence the bare <video> (see fpgas.online-cam
 * docs/2026-08-31-low-latency-research.md).
 */

(function () {
  const CSS = `
    .whep-live { position: relative; display: none; }
    .whep-live.whep-active { display: block; }
    .whep-live video { display: block; width: 100%; height: 100%; background: #000; }
    .whep-btn { position: absolute; z-index: 10; top: 6px; right: 6px;
      padding: 2px 8px; font-size: 12px; cursor: pointer;
      background: rgba(0,0,0,.65); color: #fff; border: 1px solid #888;
      border-radius: 3px; }
    .whep-goto-live { position: absolute; z-index: 10; top: 6px; right: 6px; }
  `;

  function setup(wrap) {
    const hlsEl = document.getElementById(wrap.dataset.hlsId);
    // NB: a top-level `class` in a classic script is a lexical global,
    // not a window property, so probe with typeof.
    if (!hlsEl || typeof MediaMTXWebRTCReader === "undefined") return;
    // video.js replaces the <video> with a wrapping <div class="video-js">
    // once initialised; hide/show that container, whichever it is by now.
    const hlsBox = () => hlsEl.closest(".video-js") || hlsEl;
    const hlsPlayer = () =>
      (window.videojs && window.videojs.getPlayer && window.videojs.getPlayer(hlsEl.id)) || null;

    const video = document.createElement("video");
    video.muted = true;
    video.autoplay = true;
    video.playsInline = true;
    wrap.appendChild(video);

    const rewindBtn = document.createElement("button");
    rewindBtn.className = "whep-btn";
    rewindBtn.textContent = "\u23ea rewind";
    rewindBtn.title = "Switch to the buffered player (seekable, more delay)";
    wrap.appendChild(rewindBtn);

    const liveBtn = document.createElement("button");
    liveBtn.className = "whep-btn whep-goto-live";
    liveBtn.textContent = "\u26a1 live";
    liveBtn.title = "Switch to the low-latency live view";
    liveBtn.hidden = true;
    // needs a positioned ancestor over the video.js box
    const hlsParent = hlsBox().parentElement;
    if (hlsParent && getComputedStyle(hlsParent).position === "static") {
      hlsParent.style.position = "relative";
    }
    if (hlsParent) hlsParent.appendChild(liveBtn);

    let haveTrack = false;
    let wantLive = true; // user intent; rewind button flips it

    function show(live) {
      const p = hlsPlayer();
      if (live && haveTrack) {
        wrap.classList.add("whep-active");
        hlsBox().style.display = "none";
        liveBtn.hidden = true;
        if (p && !p.paused()) p.pause(); // stop background segment fetches
        video.play().catch(() => {});
      } else {
        wrap.classList.remove("whep-active");
        hlsBox().style.display = "";
        liveBtn.hidden = !haveTrack; // only offer "live" if it works
        if (p && p.paused()) p.play();
      }
    }

    rewindBtn.addEventListener("click", () => { wantLive = false; show(false); });
    liveBtn.addEventListener("click", () => { wantLive = true; show(true); });

    const reader = new MediaMTXWebRTCReader({
      url: new URL(wrap.dataset.whepUrl, window.location.href).toString(),
      onError: () => {
        // reader retries by itself every few seconds; meanwhile fall back
        haveTrack = false;
        show(false);
      },
      onTrack: (evt) => {
        try {
          // keep the receive jitter buffer small: this is a static scene
          // and the whole point is minimal glass-to-glass delay
          evt.receiver.jitterBufferTarget = 50;
        } catch (e) { /* not supported everywhere */ }
        video.srcObject = evt.streams[0];
        haveTrack = true;
        show(wantLive);
      },
    });
    window.addEventListener("beforeunload", () => reader.close());
  }

  window.addEventListener("load", () => {
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);
    document.querySelectorAll(".whep-live[data-whep-url]").forEach(setup);
  });
})();
