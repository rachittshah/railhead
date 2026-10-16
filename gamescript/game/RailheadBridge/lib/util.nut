/* Shared helpers (owned by the core; query/action modules may use but not edit). */
Util <- {
	/* Tiles travel as integer indices (index = y * MapSizeX + x). Returns null if invalid. */
	function Tile(a, key) {
		if (!(key in a)) return null;
		local t = a[key];
		if (typeof t != "integer" || !GSMap.IsValidTile(t)) return null;
		return t;
	}

	function XY(t) { return { x = GSMap.GetTileX(t), y = GSMap.GetTileY(t) }; }

	/* Last command error as {code, msg}; code is the GSError constant name, e.g. ERR_AREA_NOT_CLEAR. */
	function LastError() {
		return { code = GSError.GetLastErrorString() };
	}

	function HasCompany(br) { return br.company >= 0; }

	/* OpenTTD's Squirrel has no format(); zero-pad by hand. */
	function Pad2(n) { return (n < 10 ? "0" : "") + n; }
}
