// Single source of shared, mutable app state. Modules read and write these fields directly:
// an object (rather than exported `let`s) so reassignments cross module boundaries cleanly,
// since ES module imports are read-only bindings.
export const state = {
  chatId: null,
  currentUserId: null,
  theme: 'light',
  map: null,
  tileLayer: null,
  routeGroup: null,
  markerGroup: null,
  allPins: [],
  totalPins: 0,
  currentPage: 1,
  currentFilterParams: null,
  skipBoundsFit: false,
  videoMarkers: [],   // {video, lat, lng, fileId, pins} per rendered cluster marker on the map
  detailPins: null,   // pins shown in the open detail overlay (null when closed)
  trips: [],          // {id, name, created_at, closed_at, pin_count} from /api/pins-meta, newest first
  selectedTripId: null,  // trip chip currently selected in the filter panel (null = all)
  initialTripId: null,   // optional deep-link trip id from startapp=c<chat>_t<trip>
  entitlements: { has_group_pass: false, group_pass_expires_at: null, unlocked_trip_ids: [] },
  pricing: { reel_stars: 100, group_pass_stars: 250, group_pass_days: 30 },
};
