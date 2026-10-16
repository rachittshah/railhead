/* Player actions for the bound company (docs/DESIGN.md §3). Handler: function H_<group>_<verb>(br, id, a).
 *
 * Every handler: parse + validate args (ERR_BAD_ARG with a precise message) -> require a bound company
 * (ERR_NO_COMPANY) -> run the player's command(s) in GSCompanyMode, inside GSTestMode when dry_run=true
 * -> reply {cost, ...ids} on success, {dry_run, cost, affordable} for a dry run, or
 * Err(<GSError name>, <hint naming a likely fix>) when the engine refuses.
 *
 * Squirrel rules here: never issue commands from .call()/.acall()/valuators (the VM cannot suspend
 * through a native frame); replies carry ints/strings/bools/arrays/tables only (no floats).
 */
Action <- {
	/* Session state (re-applied before every build; ScriptStorage defaults to INVALID types). */
	rail_type = -1,
	road_type = -1,

	/* ------------------------------------------------------------ enums (name -> GS constant) */
	TRACKS = {
		ne_sw = GSRail.RAILTRACK_NE_SW, nw_se = GSRail.RAILTRACK_NW_SE, nw_ne = GSRail.RAILTRACK_NW_NE,
		sw_se = GSRail.RAILTRACK_SW_SE, nw_sw = GSRail.RAILTRACK_NW_SW, ne_se = GSRail.RAILTRACK_NE_SE
	},
	AXES = { x = GSRail.RAILTRACK_NE_SW, y = GSRail.RAILTRACK_NW_SE },
	SIGNALS = {
		normal = GSRail.SIGNALTYPE_NORMAL, entry = GSRail.SIGNALTYPE_ENTRY, exit = GSRail.SIGNALTYPE_EXIT,
		combo = GSRail.SIGNALTYPE_COMBO, pbs = GSRail.SIGNALTYPE_PBS, pbs_oneway = GSRail.SIGNALTYPE_PBS_ONEWAY,
		normal_twoway = GSRail.SIGNALTYPE_NORMAL_TWOWAY, entry_twoway = GSRail.SIGNALTYPE_ENTRY_TWOWAY,
		exit_twoway = GSRail.SIGNALTYPE_EXIT_TWOWAY, combo_twoway = GSRail.SIGNALTYPE_COMBO_TWOWAY
	},
	STOP_KINDS = { bus = GSRoad.ROADVEHTYPE_BUS, truck = GSRoad.ROADVEHTYPE_TRUCK },
	AIRPORTS = {
		small = GSAirport.AT_SMALL, large = GSAirport.AT_LARGE, metropolitan = GSAirport.AT_METROPOLITAN,
		international = GSAirport.AT_INTERNATIONAL, commuter = GSAirport.AT_COMMUTER,
		intercon = GSAirport.AT_INTERCON, heliport = GSAirport.AT_HELIPORT,
		helistation = GSAirport.AT_HELISTATION, helidepot = GSAirport.AT_HELIDEPOT
	},
	VEHICLE_TYPES = { rail = GSVehicle.VT_RAIL, road = GSVehicle.VT_ROAD, water = GSVehicle.VT_WATER },
	CORNERS = {
		n = GSTile.SLOPE_N, e = GSTile.SLOPE_E, s = GSTile.SLOPE_S, w = GSTile.SLOPE_W,
		nw = GSTile.SLOPE_NW, ne = GSTile.SLOPE_NE, sw = GSTile.SLOPE_SW, se = GSTile.SLOPE_SE,
		ns = GSTile.SLOPE_NS, ew = GSTile.SLOPE_EW, all = GSTile.SLOPE_ELEVATED
	},
	ORDER_FLAGS = {
		non_stop = GSOrder.OF_NON_STOP_INTERMEDIATE, via = GSOrder.OF_NON_STOP_DESTINATION,
		unload = GSOrder.OF_UNLOAD, transfer = GSOrder.OF_TRANSFER, no_unload = GSOrder.OF_NO_UNLOAD,
		full_load = GSOrder.OF_FULL_LOAD, full_load_any = GSOrder.OF_FULL_LOAD_ANY, no_load = GSOrder.OF_NO_LOAD,
		service = GSOrder.OF_SERVICE_IF_NEEDED, stop_in_depot = GSOrder.OF_STOP_IN_DEPOT,
		go_depot = GSOrder.OF_GOTO_NEAREST_DEPOT
	},
	TOWN_ACTIONS = {
		advertise_small = GSTown.TOWN_ACTION_ADVERTISE_SMALL, advertise_medium = GSTown.TOWN_ACTION_ADVERTISE_MEDIUM,
		advertise_large = GSTown.TOWN_ACTION_ADVERTISE_LARGE, road_rebuild = GSTown.TOWN_ACTION_ROAD_REBUILD,
		statue = GSTown.TOWN_ACTION_BUILD_STATUE, fund_buildings = GSTown.TOWN_ACTION_FUND_BUILDINGS,
		exclusive_rights = GSTown.TOWN_ACTION_BUY_RIGHTS, bribe = GSTown.TOWN_ACTION_BRIBE
	},

	/* ------------------------------------------------------------ error hints (GSError name -> fix) */
	HINTS = {
		ERR_NOT_ENOUGH_CASH = "not enough money: price it with dry_run, raise the loan with company.loan, or wait for income",
		ERR_LOCAL_AUTHORITY_REFUSES = "the town refuses (rating too low): build further from town, avoid demolishing/clearing trees, or use town.action advertise_*",
		ERR_ALREADY_BUILT = "already built: nothing to do here (move on to the next piece)",
		ERR_AREA_NOT_CLEAR = "something is in the way (house, industry, rail/road, station, water): pick another tile or land.demolish it first",
		ERR_OWNED_BY_ANOTHER_COMPANY = "the tile belongs to someone else: route around it",
		ERR_NAME_IS_NOT_UNIQUE = "that name is already used: choose another",
		ERR_FLAT_LAND_REQUIRED = "needs flat land: land.level the site or pick a flat tile",
		ERR_LAND_SLOPED_WRONG = "slope is incompatible with this piece: land.level / land.raise / land.lower it, or run straight along the slope",
		ERR_VEHICLE_IN_THE_WAY = "a vehicle is on the tile: wait a few ticks and retry",
		ERR_SITE_UNSUITABLE = "site unsuitable: docks need a straight sloped coast tile facing water, locks a straight slope on a waterway",
		ERR_TOO_CLOSE_TO_EDGE = "too close to the map edge: move inward",
		ERR_STATION_TOO_SPREAD_OUT = "joining would spread the station too far: build a separate station (join=-1) or place it closer",
		ERR_BRIDGE_TOO_LOW = "a bridge above is too low: pick another tile",
		ERR_PRECONDITION_FAILED = "the engine rejected the arguments (wrong tile type, orientation, ownership or vehicle state): inspect the tile/vehicle first",
		ERR_PRECONDITION_INVALID_COMPANY = "no valid company to act for",
		ERR_PRECONDITION_STRING_TOO_LONG = "text too long: shorten it",
		ERR_NEWGRF_SUPPLIED_ERROR = "a NewGRF refused this: try another tile or type",
		ERR_UNKNOWN = "refused without a specific reason: try a neighbouring tile or check what is on the tile",
		ERR_VEHICLE_TOO_MANY = "vehicle limit reached: sell vehicles first",
		ERR_VEHICLE_NOT_AVAILABLE = "engine not available to this company (not introduced yet or retired): pick another engine",
		ERR_VEHICLE_BUILD_DISABLED = "this vehicle type is disabled by the game settings",
		ERR_VEHICLE_WRONG_DEPOT = "the depot does not match the engine (rail/road/water/air or track/road type): use a matching depot",
		ERR_VEHICLE_CANNOT_SEND_TO_DEPOT = "no reachable depot (or already heading to one): build a depot on its route",
		ERR_VEHICLE_CANNOT_START_STOP = "cannot start/stop it now (crashed, or needs orders)",
		ERR_VEHICLE_CANNOT_TURN = "cannot reverse/turn here",
		ERR_VEHICLE_CANNOT_REFIT = "cannot refit: engine cannot carry that cargo, or it is not stopped in a depot (vehicle.depot, wait, vehicle.stop)",
		ERR_VEHICLE_IS_DESTROYED = "the vehicle is destroyed (crashed): sell or replace it",
		ERR_VEHICLE_NOT_IN_DEPOT = "must be stopped inside a depot: vehicle.depot, wait until it arrives, then vehicle.stop",
		ERR_VEHICLE_IN_FLIGHT = "the aircraft is flying: wait until it lands",
		ERR_VEHICLE_NO_POWER = "the train has no power on this track type: use matching rail or another engine",
		ERR_VEHICLE_TOO_LONG = "train too long: sell wagons",
		ERR_STATION_TOO_CLOSE_TO_ANOTHER_STATION = "too close to another station: join it (join=<station id>) or move away",
		ERR_STATION_TOO_MANY_STATIONS = "station limit reached: remove unused stations",
		ERR_STATION_TOO_MANY_STATIONS_IN_TOWN = "the town allows no more stations: build elsewhere or join an existing station",
		ERR_ORDER_TOO_MANY = "order list is full: remove orders",
		ERR_ORDER_TOO_FAR_AWAY_FROM_PREVIOUS_DESTINATION = "too far from the previous destination for this aircraft: add an intermediate stop",
		ERR_ORDER_AIRCRAFT_NOT_ENOUGH_RANGE = "aircraft range too short for these orders",
		ERR_UNSUITABLE_TRACK = "no suitable rail on that tile for this (missing track/signal or wrong direction): check the track piece",
		ERR_CROSSING_ON_ONEWAY_ROAD = "cannot cross a one-way road: route elsewhere",
		ERR_RAILTYPE_DISALLOWS_CROSSING = "this rail type cannot cross roads: bridge or tunnel instead",
		ERR_ROAD_WORKS_IN_PROGRESS = "road works in progress: wait some days and retry",
		ERR_ROAD_DRIVE_THROUGH_WRONG_DIRECTION = "drive-through stop must be aligned with the road: set front_tile along the road",
		ERR_ROAD_CANNOT_BUILD_ON_TOWN_ROAD = "cannot build a drive-through stop on this town road: use a bay stop beside the road",
		ERR_ROAD_ONE_WAY_ROADS_CANNOT_HAVE_JUNCTIONS = "one-way roads cannot have junctions",
		ERR_ROADTYPE_DISALLOWS_CROSSING = "this road type cannot cross rail: bridge or tunnel instead",
		ERR_UNSUITABLE_ROAD = "no suitable road on that tile",
		ERR_BRIDGE_TYPE_UNAVAILABLE = "bridge type unavailable or wrong length for this span: pick another type",
		ERR_BRIDGE_CANNOT_END_IN_WATER = "bridge ends must be on land",
		ERR_BRIDGE_HEADS_NOT_ON_SAME_HEIGHT = "bridge ends differ in height: pick ends of equal height (sloped up toward the span) or level the land",
		ERR_TUNNEL_CANNOT_BUILD_ON_WATER = "tunnels cannot start on water",
		ERR_TUNNEL_START_SITE_UNSUITABLE = "tunnel start needs a straight inclined tile facing into the hill",
		ERR_TUNNEL_ANOTHER_TUNNEL_IN_THE_WAY = "another tunnel is in the way",
		ERR_TUNNEL_END_SITE_UNSUITABLE = "the far end of the tunnel is unsuitable: start elsewhere",
		ERR_TILE_TOO_HIGH = "already at maximum height",
		ERR_TILE_TOO_LOW = "already at sea level",
		ERR_AREA_ALREADY_FLAT = "already flat: nothing to do",
		ERR_EXCAVATION_WOULD_DAMAGE = "a tunnel below prevents this terraforming",
		ERR_LIMIT_REACHED = "terraform/clear limit reached for now: let time pass (wait) and retry",
		ERR_MARINE_MUST_BE_BUILT_ON_WATER = "must be built on water",
		ERR_WAYPOINT_TOO_CLOSE_TO_ANOTHER_WAYPOINT = "too close to another waypoint",
		ERR_WAYPOINT_ADJOINS_MULTIPLE_WAYPOINTS = "adjoins more than one waypoint",
		ERR_SIGN_TOO_MANY_SIGNS = "too many signs"
	},

	/* ------------------------------------------------------------ handler scaffolding */

	/* Parse `a` against spec = [[name, type(, default)], ...]; a 3rd element marks the param optional.
	 * Types: "tile" "int" "str" "bool" "vehicle" "engine" "town" "cargo" "flags" or an enum table.
	 * Throws {rh_bad=msg}. Unknown keys are rejected (dry_run is always allowed). */
	function Parse(a, spec) {
		local p = {};
		local known = { dry_run = true };
		foreach (s in spec) {
			local name = s[0];
			known[name] <- true;
			local has = (name in a) && a[name] != null;
			if (!has) {
				if (s.len() < 3) Action.Bad("missing required argument '" + name + "'");
				p[name] <- s[2];
				continue;
			}
			p[name] <- Action.Coerce(name, a[name], s[1]);
		}
		foreach (k, _ in a) {
			if (!(k in known)) Action.Bad("unknown argument '" + k + "'");
		}
		if (("dry_run" in a) && a.dry_run != null && typeof a.dry_run != "bool") Action.Bad("dry_run must be a bool");
		return p;
	}

	function Coerce(name, v, t) {
		if (typeof t == "table") {
			if (typeof v != "string" || !(v in t)) Action.Bad(name + " must be one of " + Action.Keys(t) + ", got " + v);
			return t[v];
		}
		switch (t) {
			case "tile":
				if (typeof v != "integer" || !GSMap.IsValidTile(v)) Action.Bad(name + " is not a valid tile index (" + v + "); map is " + GSMap.GetMapSizeX() + "x" + GSMap.GetMapSizeY());
				return v;
			case "int":
				if (typeof v != "integer") Action.Bad(name + " must be an integer");
				return v;
			case "str":
				if (typeof v != "string") Action.Bad(name + " must be a string");
				return v;
			case "bool":
				if (typeof v != "bool") Action.Bad(name + " must be a bool");
				return v;
			case "vehicle":
				if (typeof v != "integer" || !GSVehicle.IsValidVehicle(v)) Action.Bad(name + " " + v + " is not one of your vehicles");
				if (!GSVehicle.IsPrimaryVehicle(v)) Action.Bad(name + " " + v + " is a wagon, not a primary vehicle");
				return v;
			case "engine":
				if (typeof v != "integer" || !GSEngine.IsValidEngine(v)) Action.Bad(name + " " + v + " is not an engine available to you");
				return v;
			case "town":
				if (typeof v != "integer" || !GSTown.IsValidTown(v)) Action.Bad(name + " " + v + " is not a town id");
				return v;
			case "cargo":
				return Action.CargoOf(name, v);
			case "flags": {
				if (typeof v != "array") Action.Bad(name + " must be a list of order flags");
				local f = GSOrder.OF_NONE;
				foreach (x in v) {
					if (typeof x != "string" || !(x in Action.ORDER_FLAGS)) Action.Bad("unknown order flag " + x + "; known: " + Action.Keys(Action.ORDER_FLAGS));
					f = f | Action.ORDER_FLAGS[x];
				}
				return f;
			}
		}
		Action.Bad("internal: unknown param type " + t);
	}

	/* Cargo by id or by label ("PASS", "COAL", ...). */
	function CargoOf(name, v) {
		if (typeof v == "integer") {
			if (!GSCargo.IsValidCargo(v)) Action.Bad(name + " " + v + " is not a cargo id");
			return v;
		}
		if (typeof v == "string") {
			local want = v.toupper();
			foreach (c, _ in GSCargoList()) {
				if (GSCargo.GetCargoLabel(c) == want) return c;
			}
		}
		Action.Bad(name + " " + v + " is not a cargo id or label (e.g. PASS, MAIL, COAL)");
	}

	function Keys(t) {
		local ks = [];
		foreach (k, _ in t) ks.append(k);
		ks.sort();
		local s = "";
		foreach (i, k in ks) s += (i ? "|" : "") + k;
		return s;
	}

	function Bad(msg) { throw { rh_bad = msg }; }

	/* Enter the company (and test) mode and start counting cost. Returns ctx or null after replying.
	 * Keep the returned table in a local for the whole handler: its members own the modes. */
	function Begin(br, id, a, spec) {
		if (br.company < 0) { Proto.Err(id, "ERR_NO_COMPANY", "no company is bound to the model"); return null; }
		local ctx = { id = id, company = br.company, dry = false, p = null, cm = GSCompanyMode(br.company), tm = null, acc = null };
		try {
			ctx.p = Action.Parse(a, spec);
		} catch (e) {
			if (typeof e == "table" && ("rh_bad" in e)) { Proto.Err(id, "ERR_BAD_ARG", e.rh_bad); return null; }
			throw e;
		}
		ctx.dry = ("dry_run" in a) && a.dry_run == true;
		if (ctx.dry) ctx.tm = GSTestMode();
		ctx.acc = GSAccounting();
		return ctx;
	}

	/* Semantic validation after Parse (needs company mode): reply ERR_BAD_ARG and return true if `bad`. */
	function Reject(ctx, msg) { Proto.Err(ctx.id, "ERR_BAD_ARG", msg); return true; }

	function Ok(ctx, extra) {
		local cost = ctx.acc.GetCosts();
		if (ctx.dry) {
			return Proto.Ok(ctx.id, { dry_run = true, cost = cost,
				affordable = cost <= GSCompany.GetBankBalance(GSCompany.COMPANY_SELF) });
		}
		local out = (extra == null) ? {} : extra;
		out.cost <- cost;
		Proto.Ok(ctx.id, out);
	}

	/* The engine refused: typed code + hint (+ what is on the target tile when the code is vague). */
	function Fail(ctx) {
		local code = GSError.GetLastErrorString();
		if (code == null || code == "ERR_NONE") code = "ERR_UNKNOWN";
		local hint = (code in Action.HINTS) ? Action.HINTS[code] : "the engine refused this command";
		if (code == "ERR_UNKNOWN" || code == "ERR_AREA_NOT_CLEAR" || code == "ERR_PRECONDITION_FAILED") {
			foreach (k in ["tile", "from_tile", "start", "depot_tile"]) {
				if ((k in ctx.p) && ctx.p[k] != null) { hint += " [" + k + " " + Action.Describe(ctx.p[k]) + "]"; break; }
			}
		}
		Proto.Err(ctx.id, code, hint);
	}

	/* Short description of what occupies a tile, for error hints. */
	function Describe(t) {
		local what = [];
		if (GSTile.IsStationTile(t)) what.append("station " + GSStation.GetStationID(t));
		else if (GSRail.IsRailDepotTile(t) || GSRoad.IsRoadDepotTile(t) || GSMarine.IsWaterDepotTile(t)) what.append("depot");
		else {
			if (GSRail.IsRailTile(t)) what.append("rail");
			if (GSRoad.IsRoadTile(t)) what.append("road");
		}
		if (GSBridge.IsBridgeTile(t)) what.append("bridge");
		if (GSTunnel.IsTunnelTile(t)) what.append("tunnel");
		if (GSTile.IsHouseTile(t)) what.append("house");
		if (GSIndustry.IsValidIndustry(GSIndustry.GetIndustryID(t))) what.append("industry");
		if (GSTile.IsWaterTile(t)) what.append("water");
		if (GSTile.HasTreeOnTile(t)) what.append("trees");
		if (GSTile.GetSlope(t) != GSTile.SLOPE_FLAT) what.append("sloped");
		local o = GSTile.GetOwner(t);
		if (o != GSCompany.COMPANY_INVALID) what.append(GSCompany.IsMine(o) ? "yours" : "owned by company " + o);
		local s = GSMap.GetTileX(t) + "," + GSMap.GetTileY(t) + ":";
		if (what.len() == 0) return s + " clear land";
		foreach (i, w in what) s += (i ? ", " : " ") + w;
		return s;
	}

	function Done(ctx, ok, extra) {
		if (!ok) return Action.Fail(ctx);
		Action.Ok(ctx, ctx.dry ? null : extra);
	}


	/* Session state for the core's Save()/Load() (so forks/loaded games keep the chosen rail/road type). */
	function SaveState() { return { rail_type = Action.rail_type, road_type = Action.road_type }; }
	function LoadState(d) {
		if (typeof d != "table") return;
		if ("rail_type" in d) Action.rail_type = d.rail_type;
		if ("road_type" in d) Action.road_type = d.road_type;
	}

	/* ------------------------------------------------------------ geometry / shared helpers */
	function X(t) { return GSMap.GetTileX(t); }
	function Y(t) { return GSMap.GetTileY(t); }
	function Abs(v) { return v < 0 ? -v : v; }
	function Sgn(v) { return v > 0 ? 1 : (v < 0 ? -1 : 0); }
	function Adjacent(t1, t2) { return GSMap.DistanceManhattan(t1, t2) == 1; }
	function Straight(t1, t2) { return t1 != t2 && (Action.X(t1) == Action.X(t2) || Action.Y(t1) == Action.Y(t2)); }

	/* Tile at (x, y) or null when off the map. */
	function At(x, y) {
		if (x < 0 || y < 0 || x >= GSMap.GetMapSizeX() || y >= GSMap.GetMapSizeY()) return null;
		local t = GSMap.GetTileIndex(x, y);
		return GSMap.IsValidTile(t) ? t : null;
	}

	/* Station to join: null -> adjacent own station (GUI default), -1 -> force new, id -> that station. */
	function JoinId(join) {
		if (join == null) return GSStation.STATION_JOIN_ADJACENT;
		if (join == -1) return GSStation.STATION_NEW;
		return GSStation.IsValidStation(join) ? join : null;
	}

	function UseRailType() {
		if (Action.rail_type < 0 || !GSRail.IsRailTypeAvailable(Action.rail_type)) {
			local l = GSRailTypeList();
			l.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
			Action.rail_type = l.IsEmpty() ? -1 : l.Begin();
		}
		GSRail.SetCurrentRailType(Action.rail_type);
	}

	function UseRoadType() {
		if (Action.road_type < 0 || !GSRoad.IsRoadTypeAvailable(Action.road_type)) {
			Action.road_type = GSRoad.ROADTYPE_ROAD;
			if (!GSRoad.IsRoadTypeAvailable(Action.road_type)) {
				local l = GSRoadTypeList(GSRoad.ROADTRAMTYPES_ROAD);
				l.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
				Action.road_type = l.IsEmpty() ? -1 : l.Begin();
			}
		}
		GSRoad.SetCurrentRoadType(Action.road_type);
	}

	/* True if `engine` carries or can be refitted to `cargo`. */
	function EngineTakes(engine, cargo) {
		return GSEngine.GetCargoType(engine) == cargo || GSEngine.CanRefitCargo(engine, cargo);
	}

	/* True if any part of vehicle `v` (trains: any wagon) carries or can be refitted to `cargo`. */
	function VehicleTakes(v, cargo) {
		local n = GSVehicle.GetNumWagons(v);
		for (local i = 0; i < n; i++) {
			if (Action.EngineTakes(GSVehicle.GetWagonEngineType(v, i), cargo)) return true;
		}
		return false;
	}

	function IsOwnDepot(company, t) {
		if (GSTile.GetOwner(t) != company) return false;
		return GSRoad.IsRoadDepotTile(t) || GSRail.IsRailDepotTile(t) || GSMarine.IsWaterDepotTile(t) || GSAirport.IsHangarTile(t);
	}

	/* Mirror of ScriptRail's SimulateDrag end-tile adjustment (script_rail.cpp). */
	function DragEnd(from, tile, to) {
		local W = GSMap.GetMapSizeX();
		local fx = Action.X(from), fy = Action.Y(from), x = Action.X(tile), y = Action.Y(tile);
		local tx = Action.X(to), ty = Action.Y(to);
		local diag = Action.Abs(Action.Abs(tx - x) - Action.Abs(ty - y));
		local cx = Action.Sgn(tx - x), cy = Action.Sgn(ty - y);
		if (fy == ty) return to - cx;
		if (fx == tx) return to - W * cy;
		if (fy != y) return diag != 0 ? to - cx : to - W * cy;
		return diag == 0 ? to - cx : to - W * cy;
	}

	/* GS BuildRail(from, tile, to) takes the tile *after* the last one; find it so the drag ends on `last`. */
	function DragAfter(prev, first, last) {
		local lx = Action.X(last), ly = Action.Y(last);
		local sx = Action.X(first) - Action.X(prev), sy = Action.Y(first) - Action.Y(prev);
		local cands = [[lx + sx, ly + sy], [lx + 1, ly], [lx - 1, ly], [lx, ly + 1], [lx, ly - 1]];
		foreach (c in cands) {
			local to = Action.At(c[0], c[1]);
			if (to == null || GSMap.DistanceManhattan(first, to) < 1) continue;
			local diag = Action.Abs(Action.Abs(Action.X(to) - Action.X(first)) - Action.Abs(Action.Y(to) - Action.Y(first)));
			local inline = (Action.X(prev) == Action.X(first) && Action.X(first) == Action.X(to)) ||
				(Action.Y(prev) == Action.Y(first) && Action.Y(first) == Action.Y(to));
			if (diag > 1 && !inline) continue;
			if (Action.DragEnd(prev, first, to) == last) return to;
		}
		return null;
	}

	/* ------------------------------------------------------------ rail */
	function H_rail_set_type(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["railtype", "int"]]);
		if (ctx == null) return;
		local rt = ctx.p.railtype;
		if (!GSRail.IsRailTypeAvailable(rt)) {
			local avail = "";
			foreach (t, _ in GSRailTypeList()) avail += (avail.len() ? "," : "") + t;
			return Action.Reject(ctx, "rail type " + rt + " is not available to you; available: " + avail);
		}
		if (!ctx.dry) Action.rail_type = rt;
		Action.Ok(ctx, { railtype = rt, name = GSRail.GetName(rt) });
	}

	function H_rail_track(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["track", Action.TRACKS]]);
		if (ctx == null) return;
		Action.UseRailType();
		Action.Done(ctx, GSRail.BuildRailTrack(ctx.p.tile, ctx.p.track), {});
	}

	function H_rail_line(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["from_tile", "tile"], ["to_tile", "tile"], ["start_dir_tile", "tile", null]]);
		if (ctx == null) return;
		local p = ctx.p;
		local dx = Action.X(p.to_tile) - Action.X(p.from_tile), dy = Action.Y(p.to_tile) - Action.Y(p.from_tile);
		local straight = dx == 0 || dy == 0;
		if (!straight && Action.Abs(Action.Abs(dx) - Action.Abs(dy)) > 1) {
			return Action.Reject(ctx, "rail.line must be straight (same x or y) or diagonal (|dx| == |dy|); got dx=" + dx + " dy=" + dy);
		}
		local prev = p.start_dir_tile;
		if (prev == null) {
			if (p.from_tile == p.to_tile) return Action.Reject(ctx, "from_tile == to_tile: give start_dir_tile (the neighbour the track comes from) or use rail.track");
			prev = straight ? Action.At(Action.X(p.from_tile) - Action.Sgn(dx), Action.Y(p.from_tile) - Action.Sgn(dy))
				: Action.At(Action.X(p.from_tile) - Action.Sgn(dx), Action.Y(p.from_tile));
			if (prev == null) return Action.Reject(ctx, "from_tile is on the map edge: give start_dir_tile");
		} else if (!Action.Adjacent(prev, p.from_tile)) {
			return Action.Reject(ctx, "start_dir_tile must be a direct neighbour of from_tile");
		}
		local after = Action.DragAfter(prev, p.from_tile, p.to_tile);
		if (after == null) return Action.Reject(ctx, "cannot drag a track from from_tile to to_tile entering from start_dir_tile; try another start_dir_tile");
		Action.UseRailType();
		Action.Done(ctx, GSRail.BuildRail(prev, p.from_tile, after), {});
	}

	function H_rail_remove(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["track", Action.TRACKS]]);
		if (ctx == null) return;
		if (!GSRail.IsRailTile(ctx.p.tile) || (GSRail.GetRailTracks(ctx.p.tile) & ctx.p.track) == 0) {
			return Proto.Err(id, "ERR_UNSUITABLE_TRACK", "there is no " + a.track + " track on that tile");
		}
		Action.Done(ctx, GSRail.RemoveRailTrack(ctx.p.tile, ctx.p.track), {});
	}

	function H_rail_station(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["axis", Action.AXES], ["platforms", "int"],
			["length", "int"], ["join", "int", null]]);
		if (ctx == null) return;
		local p = ctx.p;
		if (p.platforms < 1 || p.platforms > 255) return Action.Reject(ctx, "platforms must be 1..255");
		if (p.length < 1 || p.length > 255) return Action.Reject(ctx, "length must be 1..255");
		local join = Action.JoinId(p.join);
		if (join == null) return Action.Reject(ctx, "join " + p.join + " is not one of your stations (omit to join an adjacent one, -1 for a new station)");
		Action.UseRailType();
		local ok = GSRail.BuildRailStation(p.tile, p.axis, p.platforms, p.length, join);
		Action.Done(ctx, ok, ok && !ctx.dry ? { station = GSStation.GetStationID(p.tile) } : null);
	}

	function H_rail_depot(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["front_tile", "tile"]]);
		if (ctx == null) return;
		if (!Action.Adjacent(ctx.p.tile, ctx.p.front_tile)) return Action.Reject(ctx, "front_tile must be a direct neighbour of tile");
		Action.UseRailType();
		Action.Done(ctx, GSRail.BuildRailDepot(ctx.p.tile, ctx.p.front_tile), { depot = ctx.p.tile });
	}

	function H_rail_signal(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["front_tile", "tile"], ["type", Action.SIGNALS, GSRail.SIGNALTYPE_PBS_ONEWAY]]);
		if (ctx == null) return;
		local p = ctx.p;
		if (!Action.Adjacent(p.tile, p.front_tile)) return Action.Reject(ctx, "front_tile must be a direct neighbour of tile");
		if (!GSRail.IsRailTile(p.tile) || GSRail.IsRailStationTile(p.tile) || GSRail.IsRailWaypointTile(p.tile)) {
			return Proto.Err(id, "ERR_UNSUITABLE_TRACK", "signals go on plain rail tiles (not stations, waypoints or depots)");
		}
		Action.Done(ctx, GSRail.BuildSignal(p.tile, p.front_tile, p.type), {});
	}

	function H_rail_remove_signal(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["front_tile", "tile"]]);
		if (ctx == null) return;
		local p = ctx.p;
		if (!Action.Adjacent(p.tile, p.front_tile)) return Action.Reject(ctx, "front_tile must be a direct neighbour of tile");
		if (GSRail.GetSignalType(p.tile, p.front_tile) == GSRail.SIGNALTYPE_NONE) {
			return Proto.Err(id, "ERR_UNSUITABLE_TRACK", "no signal on that tile facing front_tile");
		}
		Action.Done(ctx, GSRail.RemoveSignal(p.tile, p.front_tile), {});
	}

	function H_rail_waypoint(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"]]);
		if (ctx == null) return;
		local t = ctx.p.tile;
		local tracks = GSRail.GetRailTracks(t);
		if (!GSRail.IsRailTile(t) || (tracks != GSRail.RAILTRACK_NE_SW && tracks != GSRail.RAILTRACK_NW_SE)) {
			return Proto.Err(id, "ERR_UNSUITABLE_TRACK", "waypoints need a rail tile with one straight track (ne_sw or nw_se); its axis follows the track");
		}
		Action.UseRailType();
		local ok = GSRail.BuildRailWaypoint(t);
		Action.Done(ctx, ok, ok && !ctx.dry ? { waypoint = GSWaypoint.GetWaypointID(t) } : null);
	}

	/* ------------------------------------------------------------ road */
	function H_road_set_type(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["roadtype", "int"]]);
		if (ctx == null) return;
		local rt = ctx.p.roadtype;
		if (!GSRoad.IsRoadTypeAvailable(rt)) return Action.Reject(ctx, "road type " + rt + " is not available to you");
		if (!ctx.dry) Action.road_type = rt;
		Action.Ok(ctx, { roadtype = rt, name = GSRoad.GetName(rt) });
	}

	function RoadSegment(br, id, a, what) {
		local ctx = Action.Begin(br, id, a, [["from_tile", "tile"], ["to_tile", "tile"]]);
		if (ctx == null) return;
		local p = ctx.p;
		if (!Action.Straight(p.from_tile, p.to_tile)) {
			return Action.Reject(ctx, "from_tile and to_tile must differ and share x or y (road is dragged in a straight line)");
		}
		Action.UseRoadType();
		local ok = false;
		if (what == 0) ok = GSRoad.BuildRoad(p.from_tile, p.to_tile);
		else if (what == 1) ok = GSRoad.BuildOneWayRoad(p.from_tile, p.to_tile);
		else ok = GSRoad.RemoveRoad(p.from_tile, p.to_tile);
		Action.Done(ctx, ok, {});
	}
	function H_road_line(br, id, a) { Action.RoadSegment(br, id, a, 0); }
	function H_road_one_way(br, id, a) { Action.RoadSegment(br, id, a, 1); }
	function H_road_remove(br, id, a) { Action.RoadSegment(br, id, a, 2); }

	function H_road_depot(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["front_tile", "tile"]]);
		if (ctx == null) return;
		if (!Action.Adjacent(ctx.p.tile, ctx.p.front_tile)) return Action.Reject(ctx, "front_tile must be a direct neighbour of tile");
		Action.UseRoadType();
		Action.Done(ctx, GSRoad.BuildRoadDepot(ctx.p.tile, ctx.p.front_tile), { depot = ctx.p.tile });
	}

	function H_road_stop(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["front_tile", "tile"], ["kind", Action.STOP_KINDS, GSRoad.ROADVEHTYPE_BUS],
			["drive_through", "bool", false], ["join", "int", null]]);
		if (ctx == null) return;
		local p = ctx.p;
		if (!Action.Adjacent(p.tile, p.front_tile)) return Action.Reject(ctx, "front_tile must be a direct neighbour of tile");
		local join = Action.JoinId(p.join);
		if (join == null) return Action.Reject(ctx, "join " + p.join + " is not one of your stations (omit to join an adjacent one, -1 for a new station)");
		Action.UseRoadType();
		local ok = p.drive_through ? GSRoad.BuildDriveThroughRoadStation(p.tile, p.front_tile, p.kind, join)
			: GSRoad.BuildRoadStation(p.tile, p.front_tile, p.kind, join);
		Action.Done(ctx, ok, ok && !ctx.dry ? { station = GSStation.GetStationID(p.tile) } : null);
	}

	/* ------------------------------------------------------------ air / water */
	function H_air_airport(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["type", Action.AIRPORTS], ["join", "int", null]]);
		if (ctx == null) return;
		local p = ctx.p;
		if (!GSAirport.IsValidAirportType(p.type)) {
			return Proto.Err(id, "ERR_TYPE_NOT_AVAILABLE", "airport type " + a.type + " is not available yet: try small, or wait for later years");
		}
		local join = Action.JoinId(p.join);
		if (join == null) return Action.Reject(ctx, "join " + p.join + " is not one of your stations");
		local ok = GSAirport.BuildAirport(p.tile, p.type, join);
		local extra = null;
		if (ok && !ctx.dry) {
			local hangar = GSAirport.GetHangarOfAirport(p.tile);
			extra = { station = GSStation.GetStationID(p.tile), hangar = GSMap.IsValidTile(hangar) ? hangar : -1 };
		}
		Action.Done(ctx, ok, extra);
	}

	function H_water_dock(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["join", "int", null]]);
		if (ctx == null) return;
		local join = Action.JoinId(ctx.p.join);
		if (join == null) return Action.Reject(ctx, "join " + ctx.p.join + " is not one of your stations");
		local ok = GSMarine.BuildDock(ctx.p.tile, join);
		Action.Done(ctx, ok, ok && !ctx.dry ? { station = GSStation.GetStationID(ctx.p.tile) } : null);
	}

	function H_water_canal(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"]]);
		if (ctx == null) return;
		Action.Done(ctx, GSMarine.BuildCanal(ctx.p.tile), {});
	}

	function H_water_lock(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"]]);
		if (ctx == null) return;
		Action.Done(ctx, GSMarine.BuildLock(ctx.p.tile), {});
	}

	function H_water_ship_depot(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["toward_tile", "tile"]]);
		if (ctx == null) return;
		if (!Action.Adjacent(ctx.p.tile, ctx.p.toward_tile)) return Action.Reject(ctx, "toward_tile must be a direct neighbour of tile (sets the depot axis)");
		Action.Done(ctx, GSMarine.BuildWaterDepot(ctx.p.tile, ctx.p.toward_tile), { depot = ctx.p.tile });
	}

	/* ------------------------------------------------------------ bridges / tunnels */
	function H_bridge_build(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["start", "tile"], ["end", "tile"], ["vehicle", Action.VEHICLE_TYPES, GSVehicle.VT_ROAD],
			["type", "int", null]]);
		if (ctx == null) return;
		local p = ctx.p;
		if (!Action.Straight(p.start, p.end)) return Action.Reject(ctx, "start and end must differ and share x or y");
		local length = GSMap.DistanceManhattan(p.start, p.end) + 1;
		local bt = p.type;
		if (bt == null) {
			local l = GSBridgeList_Length(length);
			l.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
			if (l.IsEmpty()) return Proto.Err(id, "ERR_BRIDGE_TYPE_UNAVAILABLE", "no bridge type spans " + length + " tiles yet: shorten the span");
			bt = l.Begin();
		} else if (!GSBridge.IsValidBridge(bt)) {
			return Action.Reject(ctx, "bridge type " + bt + " does not exist or is not available yet");
		}
		if (p.vehicle == GSVehicle.VT_RAIL) Action.UseRailType();
		else if (p.vehicle == GSVehicle.VT_ROAD) Action.UseRoadType();
		Action.Done(ctx, GSBridge.BuildBridge(p.vehicle, bt, p.start, p.end), { bridge_type = bt, length = length });
	}

	function H_bridge_tunnel(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["start", "tile"], ["vehicle", Action.VEHICLE_TYPES, GSVehicle.VT_ROAD]]);
		if (ctx == null) return;
		local p = ctx.p;
		if (p.vehicle == GSVehicle.VT_WATER) return Action.Reject(ctx, "tunnels are for rail or road");
		if (p.vehicle == GSVehicle.VT_RAIL) Action.UseRailType(); else Action.UseRoadType();
		local ok = GSTunnel.BuildTunnel(p.vehicle, p.start);
		Action.Done(ctx, ok, ok && !ctx.dry ? { other_end = GSTunnel.GetOtherTunnelEnd(p.start) } : null);
	}

	/* ------------------------------------------------------------ land */
	function H_land_raise(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["corner", Action.CORNERS]]);
		if (ctx == null) return;
		Action.Done(ctx, GSTile.RaiseTile(ctx.p.tile, ctx.p.corner), {});
	}

	function H_land_lower(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"], ["corner", Action.CORNERS]]);
		if (ctx == null) return;
		Action.Done(ctx, GSTile.LowerTile(ctx.p.tile, ctx.p.corner), {});
	}

	function H_land_level(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["from_tile", "tile"], ["to_tile", "tile"]]);
		if (ctx == null) return;
		Action.Done(ctx, GSTile.LevelTiles(ctx.p.from_tile, ctx.p.to_tile), {});
	}

	function H_land_demolish(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["tile", "tile"]]);
		if (ctx == null) return;
		Action.Done(ctx, GSTile.DemolishTile(ctx.p.tile), {});
	}

	/* ------------------------------------------------------------ vehicles */
	function H_vehicle_buy(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["depot_tile", "tile"], ["engine", "engine"], ["cargo", "cargo", null]]);
		if (ctx == null) return;
		local p = ctx.p;
		if (!Action.IsOwnDepot(ctx.company, p.depot_tile)) return Action.Reject(ctx, "depot_tile is not one of your depots/hangars");
		if (!GSEngine.IsBuildable(p.engine)) {
			return Proto.Err(id, "ERR_VEHICLE_NOT_AVAILABLE", Action.HINTS.ERR_VEHICLE_NOT_AVAILABLE);
		}
		if (p.cargo != null && !Action.EngineTakes(p.engine, p.cargo)) {
			return Proto.Err(id, "ERR_VEHICLE_CANNOT_REFIT", "this engine cannot carry that cargo: pick an engine for it");
		}
		local v = (p.cargo == null) ? GSVehicle.BuildVehicle(p.depot_tile, p.engine)
			: GSVehicle.BuildVehicleWithRefit(p.depot_tile, p.engine, p.cargo);
		local ok = v != GSVehicle.VEHICLE_INVALID;
		Action.Done(ctx, ok, ok && !ctx.dry ? { vehicle = v, engine = p.engine, value = GSVehicle.GetCurrentValue(v) } : null);
	}

	function H_vehicle_sell(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["vehicle", "vehicle"]]);
		if (ctx == null) return;
		Action.Done(ctx, GSVehicle.SellVehicle(ctx.p.vehicle), { vehicle = ctx.p.vehicle });
	}

	/* Idempotent start/stop (the GUI button is a toggle; a model should not have to track parity). */
	function StartStop(br, id, a, want_running) {
		local ctx = Action.Begin(br, id, a, [["vehicle", "vehicle"]]);
		if (ctx == null) return;
		local v = ctx.p.vehicle;
		local st = GSVehicle.GetState(v);
		if (st == GSVehicle.VS_CRASHED) return Proto.Err(id, "ERR_VEHICLE_IS_DESTROYED", Action.HINTS.ERR_VEHICLE_IS_DESTROYED);
		local stopped = st == GSVehicle.VS_STOPPED || st == GSVehicle.VS_IN_DEPOT;
		if (stopped != want_running) return Action.Ok(ctx, { vehicle = v, changed = false });
		Action.Done(ctx, GSVehicle.StartStopVehicle(v), { vehicle = v, changed = true });
	}
	function H_vehicle_start(br, id, a) { Action.StartStop(br, id, a, true); }
	function H_vehicle_stop(br, id, a) { Action.StartStop(br, id, a, false); }

	function H_vehicle_depot(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["vehicle", "vehicle"], ["service", "bool", false]]);
		if (ctx == null) return;
		local v = ctx.p.vehicle;
		local ok = ctx.p.service ? GSVehicle.SendVehicleToDepotForServicing(v) : GSVehicle.SendVehicleToDepot(v);
		Action.Done(ctx, ok, { vehicle = v });
	}

	function H_vehicle_refit(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["vehicle", "vehicle"], ["cargo", "cargo"]]);
		if (ctx == null) return;
		if (!Action.VehicleTakes(ctx.p.vehicle, ctx.p.cargo)) {
			return Proto.Err(id, "ERR_VEHICLE_CANNOT_REFIT", "no part of this vehicle can carry that cargo: buy a vehicle for it");
		}
		Action.Done(ctx, GSVehicle.RefitVehicle(ctx.p.vehicle, ctx.p.cargo), { vehicle = ctx.p.vehicle, cargo = ctx.p.cargo });
	}

	function H_vehicle_clone(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["depot_tile", "tile"], ["vehicle", "vehicle"], ["share_orders", "bool", false]]);
		if (ctx == null) return;
		local p = ctx.p;
		if (!Action.IsOwnDepot(ctx.company, p.depot_tile)) return Action.Reject(ctx, "depot_tile is not one of your depots/hangars");
		local v = GSVehicle.CloneVehicle(p.depot_tile, p.vehicle, p.share_orders);
		local ok = v != GSVehicle.VEHICLE_INVALID;
		Action.Done(ctx, ok, ok && !ctx.dry ? { vehicle = v } : null);
	}

	function H_vehicle_skip_order(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["vehicle", "vehicle"], ["index", "int"]]);
		if (ctx == null) return;
		local n = GSOrder.GetOrderCount(ctx.p.vehicle);
		if (ctx.p.index < 0 || ctx.p.index >= n) return Action.Reject(ctx, "index must be 0.." + (n - 1) + " (vehicle has " + n + " orders)");
		Action.Done(ctx, GSOrder.SkipToOrder(ctx.p.vehicle, ctx.p.index), {});
	}

	/* ------------------------------------------------------------ orders */
	function CheckOrder(ctx, tile, flags) {
		if ((flags & GSOrder.OF_GOTO_NEAREST_DEPOT) == 0) {
			local is_dest = GSTile.IsStationTile(tile) || GSRoad.IsRoadDepotTile(tile) || GSRail.IsRailDepotTile(tile) ||
				GSMarine.IsWaterDepotTile(tile) || GSAirport.IsHangarTile(tile);
			if (!is_dest) return "tile is not a station, waypoint, buoy or depot (go_depot ignores the tile)";
		}
		if (!GSOrder.AreOrderFlagsValid(tile, flags)) {
			return "flags are not valid for this destination; station: non_stop, via, one of unload|transfer|no_unload, " +
				"one of full_load|full_load_any|no_load (no_unload excludes no_load); depot: non_stop, service or stop_in_depot, go_depot; " +
				"waypoint: non_stop, via";
		}
		return null;
	}

	function H_order_append(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["vehicle", "vehicle"], ["tile", "tile"], ["flags", "flags", GSOrder.OF_NONE]]);
		if (ctx == null) return;
		local p = ctx.p;
		local why = Action.CheckOrder(ctx, p.tile, p.flags);
		if (why != null) return Action.Reject(ctx, why);
		local ok = GSOrder.AppendOrder(p.vehicle, p.tile, p.flags);
		Action.Done(ctx, ok, ok && !ctx.dry ? { index = GSOrder.GetOrderCount(p.vehicle) - 1, orders = GSOrder.GetOrderCount(p.vehicle) } : null);
	}

	function H_order_insert(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["vehicle", "vehicle"], ["index", "int"], ["tile", "tile"], ["flags", "flags", GSOrder.OF_NONE]]);
		if (ctx == null) return;
		local p = ctx.p;
		local n = GSOrder.GetOrderCount(p.vehicle);
		if (p.index < 0 || p.index > n) return Action.Reject(ctx, "index must be 0.." + n);
		local why = Action.CheckOrder(ctx, p.tile, p.flags);
		if (why != null) return Action.Reject(ctx, why);
		local ok = GSOrder.InsertOrder(p.vehicle, p.index, p.tile, p.flags);
		Action.Done(ctx, ok, ok && !ctx.dry ? { index = p.index, orders = GSOrder.GetOrderCount(p.vehicle) } : null);
	}

	function H_order_remove(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["vehicle", "vehicle"], ["index", "int"]]);
		if (ctx == null) return;
		local p = ctx.p;
		local n = GSOrder.GetOrderCount(p.vehicle);
		if (p.index < 0 || p.index >= n) return Action.Reject(ctx, "index must be 0.." + (n - 1) + " (vehicle has " + n + " orders)");
		local ok = GSOrder.RemoveOrder(p.vehicle, p.index);
		Action.Done(ctx, ok, ok && !ctx.dry ? { orders = GSOrder.GetOrderCount(p.vehicle) } : null);
	}

	/* Like the GUI's "delete all orders": a vehicle sharing orders just stops sharing (others keep theirs). */
	function H_order_clear(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["vehicle", "vehicle"]]);
		if (ctx == null) return;
		local v = ctx.p.vehicle;
		if (GSVehicle.HasSharedOrders(v)) {
			if (!GSOrder.UnshareOrders(v)) return Action.Fail(ctx);
		} else {
			local n = GSOrder.GetOrderCount(v);
			// In test mode nothing is removed, so test-remove each position from the back.
			for (local i = n - 1; i >= 0; i--) {
				if (!GSOrder.RemoveOrder(v, ctx.dry ? i : 0)) return Action.Fail(ctx);
			}
		}
		Action.Ok(ctx, ctx.dry ? null : { orders = GSOrder.GetOrderCount(v) });
	}

	function H_order_share(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["vehicle", "vehicle"], ["other", "vehicle"]]);
		if (ctx == null) return;
		if (ctx.p.vehicle == ctx.p.other) return Action.Reject(ctx, "a vehicle cannot share orders with itself");
		local ok = GSOrder.ShareOrders(ctx.p.vehicle, ctx.p.other);
		Action.Done(ctx, ok, ok && !ctx.dry ? { orders = GSOrder.GetOrderCount(ctx.p.vehicle) } : null);
	}

	/* ------------------------------------------------------------ company / town */
	function H_company_rename(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["name", "str"]]);
		if (ctx == null) return;
		local name = ctx.p.name;
		if (name.len() == 0) return Action.Reject(ctx, "name must not be empty");
		Action.Done(ctx, GSCompany.SetName(name), { name = name });
	}

	function H_company_loan(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["amount", "int"]]);
		if (ctx == null) return;
		local amount = ctx.p.amount;
		local step = GSCompany.GetLoanInterval();
		local maxl = GSCompany.GetMaxLoanAmount();
		if (amount < 0 || amount > maxl) return Action.Reject(ctx, "amount must be 0.." + maxl);
		if (amount % step != 0) return Action.Reject(ctx, "amount must be a multiple of the loan interval " + step);
		local bal = GSCompany.GetBankBalance(GSCompany.COMPANY_SELF);
		if (amount - GSCompany.GetLoanAmount() + bal < 0) {
			return Proto.Err(id, "ERR_NOT_ENOUGH_CASH", "cannot repay more than your cash (" + bal + "): repay less");
		}
		local ok = GSCompany.SetLoanAmount(amount);
		Action.Done(ctx, ok, { loan = GSCompany.GetLoanAmount(), money = GSCompany.GetBankBalance(GSCompany.COMPANY_SELF) });
	}

	function H_town_action(br, id, a) {
		local ctx = Action.Begin(br, id, a, [["town", "town"], ["kind", Action.TOWN_ACTIONS]]);
		if (ctx == null) return;
		local p = ctx.p;
		if (!GSTown.IsActionAvailable(p.town, p.kind)) {
			return Proto.Err(id, "ERR_TOWN_ACTION_UNAVAILABLE",
				"not available now: too little money, a statue already stands, exclusive rights are held, or the rating is too high to bribe");
		}
		Action.Done(ctx, GSTown.PerformTownAction(p.town, p.kind), { town = p.town, kind = a.kind });
	}

	/* ------------------------------------------------------------ private test probes (not player actions; not in the replay whitelist) */
	function H_rh_tile(br, id, a) {
		local t = Util.Tile(a, "tile");
		if (t == null) return Proto.Err(id, "ERR_BAD_ARG", "tile required");
		Proto.Ok(id, {
			tile = t, x = GSMap.GetTileX(t), y = GSMap.GetTileY(t), slope = GSTile.GetSlope(t),
			height = GSTile.GetMinHeight(t), max_height = GSTile.GetMaxHeight(t), buildable = GSTile.IsBuildable(t),
			water = GSTile.IsWaterTile(t), coast = GSTile.IsCoastTile(t), road = GSRoad.IsRoadTile(t),
			rail = GSRail.IsRailTile(t), tracks = GSRail.GetRailTracks(t), station = GSTile.IsStationTile(t),
			owner = GSTile.GetOwner(t), house = GSTile.IsHouseTile(t)
		});
	}

	/* First w x h rectangle (scanning rows from (x0,y0)) that is buildable, flat and level. */
	function H_rh_find_site(br, id, a) {
		local w = a.w, h = a.h;
		local x0 = ("x0" in a) ? a.x0 : 2, y0 = ("y0" in a) ? a.y0 : 2;
		for (local y = y0; y + h < GSMap.GetMapSizeY() - 1; y++) {
			for (local x = x0; x + w < GSMap.GetMapSizeX() - 1; x++) {
				local t = GSMap.GetTileIndex(x, y);
				if (!GSTile.IsBuildableRectangle(t, w, h)) continue;
				local z = GSTile.GetMinHeight(t);
				local good = true;
				for (local dy = 0; good && dy < h; dy++) {
					for (local dx = 0; good && dx < w; dx++) {
						local u = GSMap.GetTileIndex(x + dx, y + dy);
						good = GSTile.GetSlope(u) == GSTile.SLOPE_FLAT && GSTile.GetMinHeight(u) == z;
					}
				}
				if (good) return Proto.Ok(id, t);
			}
		}
		Proto.Ok(id, -1);
	}

	/* First tile (row scan) where a test-mode build of `kind` succeeds (and costs <= max_cost if given):
	 * dock | lock | tunnel_road | tunnel_rail. */
	function H_rh_probe_site(br, id, a) {
		local max_cost = ("max_cost" in a) ? a.max_cost : null;
		local cm = GSCompanyMode(br.company);
		local tm = GSTestMode();
		local acc = GSAccounting();
		Action.UseRailType();
		Action.UseRoadType();
		for (local y = 2; y < GSMap.GetMapSizeY() - 2; y++) {
			for (local x = 2; x < GSMap.GetMapSizeX() - 2; x++) {
				local t = GSMap.GetTileIndex(x, y);
				local s = GSTile.GetSlope(t);
				if (s == GSTile.SLOPE_FLAT) continue;
				acc.ResetCosts();
				local ok = false;
				if (a.kind == "dock") ok = GSTile.IsCoastTile(t) && GSMarine.BuildDock(t, GSStation.STATION_NEW);
				else if (a.kind == "lock") ok = GSMarine.BuildLock(t);
				else if (a.kind == "tunnel_road") ok = GSTunnel.BuildTunnel(GSVehicle.VT_ROAD, t);
				else if (a.kind == "tunnel_rail") ok = GSTunnel.BuildTunnel(GSVehicle.VT_RAIL, t);
				if (ok && (max_cost == null || acc.GetCosts() <= max_cost)) return Proto.Ok(id, t);
				acc.ResetCosts();
			}
		}
		Proto.Ok(id, -1);
	}

	function H_rh_engines(br, id, a) {
		local cm = GSCompanyMode(br.company);
		local vt = Action.VEHICLE_TYPES.road;
		if ("vehicle_type" in a) vt = (a.vehicle_type == "air") ? GSVehicle.VT_AIR : Action.VEHICLE_TYPES[a.vehicle_type];
		local cargo = ("cargo" in a) ? Action.CargoOf("cargo", a.cargo) : null;
		local rows = [];
		local el = GSEngineList(vt);
		el.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
		foreach (e, _ in el) {
			if (!GSEngine.IsBuildable(e)) continue;
			if (cargo != null && GSEngine.GetCargoType(e) != cargo && !GSEngine.CanRefitCargo(e, cargo)) continue;
			rows.append({ id = e, name = GSEngine.GetName(e), cargo = GSEngine.GetCargoType(e), capacity = GSEngine.GetCapacity(e),
				price = GSEngine.GetPrice(e), wagon = GSEngine.IsWagon(e) });
		}
		Proto.Rows(id, rows);
	}

	function H_rh_vehicle(br, id, a) {
		local cm = GSCompanyMode(br.company);
		local v = a.vehicle;
		if (!GSVehicle.IsValidVehicle(v)) return Proto.Err(id, "ERR_BAD_ARG", "no such vehicle");
		local orders = [];
		for (local i = 0; i < GSOrder.GetOrderCount(v); i++) {
			orders.append({ tile = GSOrder.GetOrderDestination(v, i), flags = GSOrder.GetOrderFlags(v, i) });
		}
		Proto.Ok(id, { vehicle = v, state = GSVehicle.GetState(v), location = GSVehicle.GetLocation(v),
			profit_this_year = GSVehicle.GetProfitThisYear(v), profit_last_year = GSVehicle.GetProfitLastYear(v),
			engine = GSVehicle.GetEngineType(v), shared = GSVehicle.HasSharedOrders(v), orders = orders,
			in_depot = GSVehicle.IsStoppedInDepot(v) });
	}

	function H_rh_finance(br, id, a) {
		local c = br.company;
		local out = { income = 0, delivered = 0 };
		for (local q = GSCompany.CURRENT_QUARTER; q <= GSCompany.CURRENT_QUARTER + 1; q++) {
			out.income += GSCompany.GetQuarterlyIncome(c, q);
			out.delivered += GSCompany.GetQuarterlyCargoDelivered(c, q);
		}
		Proto.Ok(id, out);
	}
};
