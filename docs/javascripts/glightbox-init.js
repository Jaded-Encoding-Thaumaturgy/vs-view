// Initialize glightbox with Material for MkDocs / Zensical support
const initLightbox = () => {
  const lightbox = GLightbox({
    touchNavigation: true,
    loop: false,
    zoomable: true,
    draggable: true,
    // Only target images or links explicitly marked with the 'lightboxOn' class
    selector: '.lightboxOn, img.lightboxOn, a.lightboxOn',
    dataSearchExclude: true,
  });
};

// Zensical/Material uses instant loading, so we subscribe to document updates
if (typeof document$ !== 'undefined') {
  document$.subscribe(() => {
    initLightbox();
  });
} else {
  // Fallback for simple page loads
  document.addEventListener('DOMContentLoaded', () => {
    initLightbox();
  });
}
