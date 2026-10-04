"use strict";

/*
 * "No camera picture from this board right now" in place of a player that
 * would spin for ever, and the player back as soon as there is a picture.
 *
 * A Pi with no camera publishes no stream, so its playlist (/live/<host>.m3u8)
 * is 404 and its video.js player never gets a frame; so is a camera's once its
 * stream has been gone long enough for the server to remove the playlist (a
 * Pi that only restarts keeps answering 200 with its last playlist). For
 * every video.js player with data-setup='{"sources": [...]}' this asks for
 * the playlist once. On a 404 the player is hidden and a static box of its
 * size stands in its place. While the box is shown the playlist is asked for
 * again every POLL_MS, only while the page is visible, and at once when the
 * page's "reset video player" button is pressed; when it answers 2xx the box
 * goes, the player is shown again and video.js is given its source again.
 * Any other answer (a server error, no answer within FETCH_MS) changes
 * nothing: the player stays as it is, or the box stays and the polling goes
 * on.
 *
 * Why the playlist and not the fleet registry: a registration's
 * peripherals.cameras lists CSI sensors only, but a Pi also streams from a
 * USB HDMI grabber (the NeTV2 boards; fpgas.online-cam gst-libcam.sh), and
 * the registry does not say whether a stream is being published. The
 * playlist is what the player itself would play.
 *
 * The player is hidden, never disposed of: its element, its id and video.js's
 * structure (wrapper <div id="video-player<N>"> around <video
 * id="video-player<N>_html5_api">) stay as they are, so whep-live.js and the
 * page's own scripts keep their references, and bringing it back is
 * player.src() with the source from data-setup. That is video.js's own
 * setSource path, so it picks its VHS engine as at setup; no <source> is
 * ever added (see tests/test_hls_source.py for the Chrome race a <source>
 * starts). The hiding is a class with display: none !important, because
 * whep-live.js writes style.display on the same element.
 *
 * The board page's low-latency (WHEP) view, when it is live, is the picture
 * and the HLS player is hidden behind it anyway: a 404 then shows no box,
 * and a box already shown goes when the WHEP view becomes live. When the
 * WHEP view is lost again (whep-live.js falls back to the HLS player), the
 * playlist is asked for again.
 */

(function () {
  const MESSAGE = "No camera picture from this board right now";
  const POLL_MS = 15000;
  // a question that gets no answer in this time counts as no answer, so one
  // stalled request cannot stop the polling
  const FETCH_MS = 10000;
  const HIDDEN = "no-picture-hidden";
  const BOX_STYLE = "box-sizing: border-box; display: flex; align-items: center; justify-content: center; " +
    "background: #222; color: #ddd; font: 16px sans-serif; text-align: center; padding: 8px; overflow: hidden;";

  const boards = [];

  function playerOf(id) {
    return (window.videojs && window.videojs.getPlayer && window.videojs.getPlayer(id)) || null;
  }

  function sourcesOf(el) {
    try {
      const sources = JSON.parse(el.dataset.setup).sources;
      return sources && sources[0] && sources[0].src ? sources : null;
    } catch (e) {
      return null;
    }
  }

  function pageHidden() {
    return document.visibilityState === "hidden";
  }

  // A length in px from an attribute or a measurement, or null.
  function px(value) {
    const n = Number(value);
    return n > 0 ? `${Math.round(n)}px` : null;
  }

  // The player's own size, taken while it is still shown: the <video>'s width
  // and height attributes, else the size video.js's player has on the page
  // (video.js drops the attributes from its wrapper), else 16:9 at that
  // width, which is how video.js sizes a player given a width only (the TT
  // board page) -- a bare <video> not yet set up is not measured, as the
  // browser's own default height for it is not the player's. The inline
  // style (the board page's "width: 100%; height: 100%") comes last and wins.
  function box(el, player) {
    const rect = player && el.getBoundingClientRect ? el.getBoundingClientRect() : { width: 0, height: 0 };
    const width = px(el.getAttribute("width")) || px(rect.width) || "100%";
    const height = px(el.getAttribute("height")) || px(rect.height);
    const size = height ? `width: ${width}; height: ${height};` : `width: ${width}; aspect-ratio: 16 / 9;`;
    const div = document.createElement("div");
    div.className = "no-picture";
    // a polite live region: read out when it appears; it takes no focus
    div.setAttribute("role", "status");
    div.textContent = MESSAGE;
    div.style.cssText = `${BOX_STYLE} ${size} ${el.getAttribute("style") || ""}`.trimEnd();
    return div;
  }

  class Board {
    constructor(el, sources) {
      this.id = el.id;
      this.sources = sources;
      this.box = null;
      this.timer = null;
      this.asking = false;
      // given its source again before it is next shown: it last saw a 404
      this.stale = false;
      this.whep = Array.from(document.querySelectorAll(".whep-live"))
        .find((wrap) => wrap.dataset.hlsId === this.id) || null;
      if (this.whep && typeof MutationObserver !== "undefined") {
        let live = this.whepLive();
        new MutationObserver(() => {
          const now = this.whepLive();
          if (now === live) return;
          live = now;
          if (live) {
            if (this.box) this.restore();
          } else {
            this.ask();
          }
        }).observe(this.whep, { attributes: true, attributeFilter: ["class"] });
      }
      const reset = document.getElementById(`refresh-${this.id}`);
      // the page's own handler reloads the player; this asks for the playlist
      // at once, so a picture that is back shows without waiting for a poll
      if (reset) reset.addEventListener("click", () => this.box && this.ask());
    }

    whepLive() {
      return !!this.whep && this.whep.classList.contains("whep-active");
    }

    ask() {
      if (this.asking) return;
      this.asking = true;
      const abort = typeof AbortController !== "undefined" ? new AbortController() : null;
      let done = false;
      const finish = (status) => {
        if (done) return;
        done = true;
        clearTimeout(limit);
        this.asking = false;
        this.answered(status);
      };
      // aborting rejects the fetch; finishing here as well covers a browser
      // without AbortController
      const limit = setTimeout(() => {
        if (abort) abort.abort();
        finish(null);
      }, FETCH_MS);
      // HEAD: only the status is wanted, not a playlist that can be tens of
      // kB; nginx answers HEAD for a file under `alias` with GET's status
      fetch(this.sources[0].src, { method: "HEAD", cache: "no-store", signal: abort ? abort.signal : undefined })
        .then((response) => response.status, () => null)
        .then(finish);
    }

    answered(status) {
      // a player that loaded a 404 has given up: it needs its source again
      if (status === 404) this.stale = true;
      if (this.whepLive()) {
        if (this.box) this.restore();
      } else if (status === 404) {
        if (!this.box) this.hide();
        this.schedule();
      } else if (status !== null && status >= 200 && status < 300) {
        if (this.box) {
          this.restore();
        } else if (this.stale) {
          this.reload();
        }
      } else if (this.box) {
        this.schedule();
      }
    }

    schedule() {
      if (this.timer !== null || !this.box || pageHidden()) return;
      this.timer = setTimeout(() => {
        this.timer = null;
        this.ask();
      }, POLL_MS);
    }

    stop() {
      if (this.timer !== null) clearTimeout(this.timer);
      this.timer = null;
    }

    hide() {
      // the <video> itself, or the wrapper video.js has given its id
      const el = document.getElementById(this.id);
      if (!el || !el.parentNode) return;
      this.box = box(el, playerOf(this.id));
      el.parentNode.insertBefore(this.box, el);
      el.classList.add(HIDDEN);
    }

    restore() {
      this.stop();
      this.box.remove();
      this.box = null;
      const el = document.getElementById(this.id);
      if (el) el.classList.remove(HIDDEN);
      // behind a live WHEP view the player stays paused (whep-live.js); it is
      // given its source when the WHEP view is lost and the playlist answers
      if (!this.whepLive()) this.reload();
    }

    reload() {
      const player = playerOf(this.id);
      if (!player) return;
      this.stale = false;
      player.src(this.sources);
      const playing = player.play();
      if (playing && playing.catch) playing.catch(() => { /* autoplay refused: the player shows its Play button */ });
    }
  }

  // video.js sets players up as soon as it sees them, often before this
  // deferred script runs, so a player is either the <video> as the page
  // wrote it or video.js's wrapper <div>, which takes the <video>'s id,
  // class and data-setup (and the <video> becomes "<id>_html5_api" with
  // class "vjs-tech", so it is not matched twice).
  function start() {
    const style = document.createElement("style");
    style.textContent = `.${HIDDEN} { display: none !important; }`;
    document.head.appendChild(style);
    document.querySelectorAll(".video-js[data-setup]").forEach((el) => {
      const sources = sourcesOf(el);
      if (!sources || !el.id) return;
      const board = new Board(el, sources);
      boards.push(board);
      board.ask();
    });
    document.addEventListener("visibilitychange", () => {
      boards.forEach((board) => {
        if (pageHidden()) {
          board.stop();
        } else if (board.box && board.timer === null) {
          board.ask();
        }
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
