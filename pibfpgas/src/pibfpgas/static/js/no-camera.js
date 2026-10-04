"use strict";

/*
 * "No camera on this board" in place of a player that would spin for ever.
 *
 * A Pi with no camera publishes no stream, so its playlist
 * (/live/<host>.m3u8) is 404 and its video.js player never gets a frame.
 * For every video.js player with data-setup='{"sources": [...]}' this
 * asks for the playlist once; when the answer is 404, the player is
 * replaced by a static box of the same size. Any other answer (200, a
 * server error, no answer at all) leaves the player exactly as it was.
 *
 * Why the playlist and not the fleet registry: a registration's
 * peripherals.cameras lists CSI sensors only, but a Pi also streams from a
 * USB HDMI grabber (the NeTV2 boards; fpgas.online-cam gst-libcam.sh), which
 * the registry does not tell apart from any other USB device. The playlist
 * is what the player itself would play.
 *
 * The check is a fetch() of its own: it does not touch the <video> element's
 * source, so video.js still sets the source from data-setup and picks its
 * own VHS engine (see tests/test_hls_source.py for why that matters in
 * Chrome). A player is replaced either before video.js has set it up (the
 * <video> is removed, so video.js never sees it) or after (the player is
 * disposed of, which removes video.js's wrapper).
 */

(function () {
  const MESSAGE = "No camera on this board";
  const BOX_STYLE = "box-sizing: border-box; display: flex; align-items: center; justify-content: center; " +
    "background: #222; color: #ddd; font: 16px sans-serif; text-align: center; padding: 8px;";

  function playlist(video) {
    try {
      const sources = JSON.parse(video.dataset.setup).sources;
      return (sources && sources[0] && sources[0].src) || null;
    } catch (e) {
      return null;
    }
  }

  function playerOf(id) {
    return (window.videojs && window.videojs.getPlayer && window.videojs.getPlayer(id)) || null;
  }

  // The player's own size: the <video>'s width and height attributes (which
  // video.js drops from its wrapper, so from the player once it is set up),
  // then the inline style (the board page's "width: 100%; height: 100%"),
  // which wins.
  function box(el, player) {
    const width = el.getAttribute("width") || (player && player.width());
    const height = el.getAttribute("height") || (player && player.height());
    const div = document.createElement("div");
    div.className = "no-camera";
    div.textContent = MESSAGE;
    div.style.cssText = `${BOX_STYLE} width: ${width}px; height: ${height}px; ${el.getAttribute("style") || ""}`
      .trimEnd();
    return div;
  }

  function replace(id, placeholder) {
    // the <video> itself, or the wrapper video.js has given its id
    const el = document.getElementById(id);
    if (!el || !el.parentNode) return;
    el.parentNode.insertBefore(placeholder, el);
    const player = playerOf(id);
    if (player) {
      player.dispose();
    } else {
      el.remove();
    }
    // the board page's "reset video player" button has nothing to reset
    const reset = document.getElementById(`refresh-${id}`);
    if (reset) reset.disabled = true;
  }

  function check(el) {
    const src = playlist(el);
    if (!src || !el.id) return;
    // the id stays the player's: video.js moves it to its wrapper
    const id = el.id;
    const placeholder = box(el, playerOf(id));
    fetch(src, { cache: "no-store" })
      .then((response) => {
        if (response.status === 404) replace(id, placeholder);
      })
      .catch(() => { /* no answer is not "no camera": keep the player */ });
  }

  // video.js sets players up as soon as it sees them, often before this
  // deferred script runs, so a player is either the <video> as the page
  // wrote it or video.js's wrapper <div>, which takes the <video>'s id,
  // class and data-setup (and the <video> becomes "<id>_html5_api" with
  // class "vjs-tech", so it is not matched twice).
  function start() {
    document.querySelectorAll(".video-js[data-setup]").forEach(check);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
