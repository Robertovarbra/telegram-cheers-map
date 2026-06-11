// Single source of shared, mutable app state. Modules read and write these fields directly:
// an object (rather than exported `let`s) so reassignments cross module boundaries cleanly,
// since ES module imports are read-only bindings.
export const state = {
  chatId: null,
  map: null,
  markerGroup: null,
  allPins: [],
  totalPins: 0,
  currentPage: 1,
  currentFilterParams: null,
  skipBoundsFit: false,
  videoMarkers: [],   // {video, lat, lng, fileId, pins} per rendered cluster marker on the map
  detailPins: null,   // pins shown in the open detail overlay (null when closed)
};
