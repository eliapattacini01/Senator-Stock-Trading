/**
 * legislators.js — shared member photo helper
 *
 * Loads legislators.json (name → bioguide_id) and exposes:
 *   window.getMemberPhoto(name)       → URL string or null
 *   window.memberAvatarHtml(name, px) → HTML string with img + fallback
 *
 * Race-condition safe: avatars rendered before the JSON finishes loading
 * are back-filled automatically via data-member attributes.
 */

window._legislators = {};

window.getMemberPhoto = function (name) {
  const id = window._legislators[name];
  if (!id) return null;
  // Official Congress Bioguide photo service
  const firstLetter = id.charAt(0).toUpperCase();
  return `https://bioguide.congress.gov/bioguide/photo/${firstLetter}/${id}.jpg`;
};

/**
 * Always emits <img data-member="..."> so _fillAvatars() can patch it later
 * if the JSON hasn't loaded yet when this is called.
 */
window.memberAvatarHtml = function (name, size) {
  size = size || 36;
  const dim     = `width:${size}px;height:${size}px;flex-shrink:0;`;
  const circle  = `border-radius:50%;`;
  const iconPx  = Math.round(size * 0.48);
  const url     = window.getMemberPhoto(name);
  const safe    = (name || "").replace(/"/g, "&quot;");

  const fallback = `<div class="member-avatar-fallback"
    style="${dim}${circle}background:#353437;display:flex;align-items:center;justify-content:center;">
    <span class="material-symbols-outlined" style="font-size:${iconPx}px;color:#8d90a1;">person</span>
  </div>`;

  if (url) {
    return `<img
      src="${url}"
      data-member="${safe}"
      data-avatar-size="${size}"
      alt=""
      style="${dim}${circle}object-fit:cover;display:block;"
      class="member-avatar-img"
      onerror="this.style.display='none';var n=this.nextElementSibling;if(n)n.style.display='flex';"
    />${fallback.replace('display:flex', 'display:none')}`;
  }

  /* No ID yet — render fallback but stamp data-member so we can upgrade it later */
  return `<img
    src=""
    data-member="${safe}"
    data-avatar-size="${size}"
    alt=""
    style="${dim}${circle}object-fit:cover;display:none;"
    class="member-avatar-img"
  />${fallback}`;
};

/** Upgrade any already-rendered placeholders once data is available */
function _fillAvatars() {
  document.querySelectorAll("img.member-avatar-img[data-member]").forEach(function (img) {
    /* Skip images that already have a working src */
    if (img.src && img.src !== window.location.href && !img.src.endsWith("/")) return;
    var name = img.getAttribute("data-member");
    var url  = window.getMemberPhoto(name);
    if (!url) return;
    var fallback = img.nextElementSibling;
    img.onload  = function () {
      img.style.display = "block";
      if (fallback && fallback.classList.contains("member-avatar-fallback"))
        fallback.style.display = "none";
    };
    img.onerror = function () { img.style.display = "none"; };
    img.src = url;
  });
}

fetch("legislators.json")
  .then(function (r) { return r.json(); })
  .then(function (data) {
    window._legislators = data;
    _fillAvatars();
    document.dispatchEvent(new CustomEvent("legislators:ready"));
  })
  .catch(function () { /* silently fail — no photos */ });
