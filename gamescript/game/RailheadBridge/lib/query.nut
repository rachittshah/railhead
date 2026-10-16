/* Read-only queries. Handler signature: function H_name(br, id, a). Reply via Proto.Ok/Rows/Err.
 * Field names id/name/tile/x/y are a stable contract used by actions, tests and the CLI.
 *
 * Rules: queries never issue commands; values are int/string/bool/array/table only (no floats:
 * percentages are integer 0..100); company-specific data is read inside GSCompanyMode(br.company)
 * because lists (stations, vehicles, depots, engines) and loan getters filter by the current company. */

/* Helpers private to the query module. */
QH <- {
	CLIMATES = ["temperate", "arctic", "tropic", "toyland"],
	TOWN_EFFECTS = ["none", "passengers", "mail", "goods", "water", "food"],

	function Climate() {
		local l = GSGame.GetLandscape();
		return (l >= 0 && l < QH.CLIMATES.len()) ? QH.CLIMATES[l] : "unknown";
	}

	function TownEffect(c) {
		local e = GSCargo.GetTownEffect(c);
		return (e >= 0 && e < QH.TOWN_EFFECTS.len()) ? QH.TOWN_EFFECTS[e] : "none";
	}

	/* Integer argument with default; non-integers are treated as absent. */
	function Int(a, key, def) {
		return (key in a && typeof a[key] == "integer") ? a[key] : def;
	}

	/* {id, tile, x, y} prefix every located row shares. */
	function Located(id, tile) {
		return { id = id, tile = tile, x = GSMap.GetTileX(tile), y = GSMap.GetTileY(tile) };
	}

	function DateTable(d) {
		return { date = d, year = GSDate.GetYear(d), month = GSDate.GetMonth(d), day = GSDate.GetDayOfMonth(d) };
	}

	TOWN_RATINGS = ["none", "appalling", "very_poor", "poor", "mediocre", "good", "very_good", "excellent", "outstanding"],

	function TownRating(t, c) {
		if (c < 0) return "none";
		local r = GSTown.GetRating(t, c);
		return (r >= 0 && r < QH.TOWN_RATINGS.len()) ? QH.TOWN_RATINGS[r] : "none";
	}

	/* First cargo with the given town effect (passengers / mail), or -1. */
	function CargoWithEffect(effect) {
		foreach (c, _ in GSCargoList()) if (GSCargo.GetTownEffect(c) == effect) return c;
		return -1;
	}

	/* Optional proximity filter from args near_tile (+ radius); null when absent. */
	function Near(a) {
		local t = Util.Tile(a, "near_tile");
		if (t == null) return null;
		return { tile = t, radius = QH.Int(a, "radius", -1) };
	}

	/* Keep rows within near.radius (Manhattan from row.tile), add `dist`, order by (dist, id).
	 * Sorting goes through GSList (native) because Squirrel comparator callbacks run inside a native
	 * frame, where an opcode-limit suspension would kill the script. */
	function ApplyNear(rows, near) {
		if (near == null) return rows;
		local by_id = {};
		local order = GSList();
		foreach (r in rows) {
			local d = GSMap.DistanceManhattan(r.tile, near.tile);
			if (near.radius >= 0 && d > near.radius) continue;
			r.dist <- d;
			by_id[r.id] <- r;
			order.AddItem(r.id, d);
		}
		order.Sort(GSList.SORT_BY_VALUE, GSList.SORT_ASCENDING);
		local out = [];
		foreach (i, _ in order) out.append(by_id[i]);
		return out;
	}

	function Pct(part, whole) { return whole > 0 ? (part * 100 / whole) : 0; }

	/* Cargo id or -1 (the API's INVALID_CARGO is 0xFF). */
	function Cargo(c) { return GSCargo.IsValidCargo(c) ? c : -1; }

	VT_NAMES = ["rail", "road", "water", "air"],
	PLANE_TYPES = { [0] = "heli", [1] = "small", [3] = "big" },

	/* Vehicle type from a.vtype ("rail"|"road"|"water"|"air"), or null. */
	function VType(a) {
		if (!("vtype" in a)) return null;
		foreach (i, n in QH.VT_NAMES) if (n == a.vtype) return i;
		return null;
	}

	function VTName(vt) { return (vt >= 0 && vt < QH.VT_NAMES.len()) ? QH.VT_NAMES[vt] : "invalid"; }

	/* Error out (returning true) when no company is bound; company queries need GSCompanyMode. */
	function NoCompany(br, id) {
		if (br.company >= 0 && GSCompany.ResolveCompanyID(br.company) != GSCompany.COMPANY_INVALID) return false;
		Proto.Err(id, "ERR_NO_COMPANY", "no company bound (bind_company first)");
		return true;
	}

	/* ---------------------------------------------------------------- tiles
	 * Map kind alphabet (one char per tile; docs in src/railhead/observe.py MAP_LEGEND):
	 *   ' ' off-map edge  . clear  ^ rough/rocks  f farmland  t trees  ~ water  , coast  L lock
	 *   T house  I industry  = road  # rail  + level crossing  S station  W rail waypoint
	 *   D depot  B bridge head / tunnel portal  O other structure (HQ, transmitter, lighthouse...) */
	BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz",
	TERRAINS = ["normal", "desert", "rainforest", "snow"],
	MAX_TILES = 4096,

	/* Tile from a.tile (index) or a.x + a.y; null if absent or off-map. */
	function TileArg(a) {
		if ("tile" in a) return Util.Tile(a, "tile");
		local x = QH.Int(a, "x", -1), y = QH.Int(a, "y", -1);
		if (x < 0 || y < 0 || x >= GSMap.GetMapSizeX() || y >= GSMap.GetMapSizeY()) return null;
		return GSMap.GetTileIndex(x, y);
	}

	/* Void border tiles: x/y == max always; x/y == 0 too with freeform edges. */
	function FreeformEdges() {
		return GSGameSettings.IsValid("construction.freeform_edges") && GSGameSettings.GetValue("construction.freeform_edges") != 0;
	}

	function IsVoid(x, y, freeform) {
		return x >= GSMap.GetMapSizeX() - 1 || y >= GSMap.GetMapSizeY() - 1 || (freeform && (x == 0 || y == 0));
	}

	/* Kind char for a non-void tile; cheap checks for the common terrain first (opcode budget). */
	function Kind(t) {
		if (GSTile.HasTreeOnTile(t)) return "t";
		if (GSTile.IsWaterTile(t)) {
			if (GSMarine.IsWaterDepotTile(t)) return "D";
			return GSMarine.IsLockTile(t) ? "L" : "~";
		}
		if (GSTile.IsCoastTile(t)) return ",";
		if (GSTile.IsBuildable(t)) { // clear land, or a single plain road piece
			if (GSRoad.IsRoadTile(t)) return "=";
			if (GSTile.IsFarmTile(t)) return "f";
			if (GSTile.IsRockTile(t) || GSTile.IsRoughTile(t)) return "^";
			return ".";
		}
		if (GSTile.IsStationTile(t)) return GSRail.IsRailWaypointTile(t) ? "W" : "S";
		if (GSTile.IsHouseTile(t)) return "T";
		if (GSIndustry.IsValidIndustry(GSIndustry.GetIndustryID(t))) return "I";
		if (GSBridge.IsBridgeTile(t) || GSTunnel.IsTunnelTile(t)) return "B";
		if (GSRail.IsRailDepotTile(t) || GSRoad.IsRoadDepotTile(t)) return "D";
		if (GSRail.IsLevelCrossingTile(t)) return "+";
		if (GSRail.IsRailTile(t)) return "#";
		if (GSRoad.IsRoadTile(t)) return "=";
		return "O";
	}

	function HeightChar(h) {
		if (h > 35) h = 35;
		if (h < 0) h = 0;
		return QH.BASE36.slice(h, h + 1);
	}

	function OwnerChar(t, company) {
		local o = GSTile.GetOwner(t);
		if (o == GSCompany.COMPANY_INVALID) return ".";
		return (o == company) ? "*" : "o";
	}

	/* Cargo acceptance (1/8 units; >= 8 means accepted) and producer counts for a station of w x h
	 * tiles at `tile` with catchment `radius`, plus every industry with a tile in that catchment. */
	function Catchment(tile, w, h, radius) {
		local accepts = [], produces = [];
		local cl = GSCargoList();
		cl.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
		foreach (c, _ in cl) {
			local acc = GSTile.GetCargoAcceptance(tile, c, w, h, radius);
			if (acc > 0) accepts.append({ cargo = c, label = GSCargo.GetCargoLabel(c), amount = acc, accepted = acc >= 8 });
			local prod = GSTile.GetCargoProduction(tile, c, w, h, radius);
			if (prod > 0) produces.append({ cargo = c, label = GSCargo.GetCargoLabel(c), producers = prod });
		}
		local x0 = GSMap.GetTileX(tile) - radius, y0 = GSMap.GetTileY(tile) - radius;
		local x1 = GSMap.GetTileX(tile) + w - 1 + radius, y1 = GSMap.GetTileY(tile) + h - 1 + radius;
		if (x0 < 0) x0 = 0;
		if (y0 < 0) y0 = 0;
		if (x1 > GSMap.GetMapSizeX() - 1) x1 = GSMap.GetMapSizeX() - 1;
		if (y1 > GSMap.GetMapSizeY() - 1) y1 = GSMap.GetMapSizeY() - 1;
		local seen = {};
		local industries = [];
		for (local y = y0; y <= y1; y++) {
			for (local x = x0; x <= x1; x++) {
				local i = GSIndustry.GetIndustryID(GSMap.GetTileIndex(x, y));
				if (!(i in seen) && GSIndustry.IsValidIndustry(i)) {
					seen[i] <- true;
					industries.append({ id = i, name = GSIndustry.GetName(i) });
				}
			}
		}
		return { accepts = accepts, produces = produces, industries = industries };
	}

	/* ---------------------------------------------------------------- assets */
	STATION_TYPES = [
		[GSStation.STATION_TRAIN, "train"], [GSStation.STATION_TRUCK_STOP, "truck"], [GSStation.STATION_BUS_STOP, "bus"],
		[GSStation.STATION_AIRPORT, "airport"], [GSStation.STATION_DOCK, "dock"]
	],
	VEHICLE_STATES = ["running", "stopped", "in_depot", "at_station", "broken", "crashed"],

	/* Transport type for a depot query, by vehicle-type index. */
	function DepotTransport(vt) {
		return [GSTile.TRANSPORT_RAIL, GSTile.TRANSPORT_ROAD, GSTile.TRANSPORT_WATER, GSTile.TRANSPORT_AIR][vt];
	}

	function StationTypes(st) {
		local out = [];
		foreach (p in QH.STATION_TYPES) if (GSStation.HasStationType(st, p[0])) out.append(p[1]);
		return out;
	}

	/* Order flags as the names order.append accepts (actions use the same vocabulary), so a model can
	 * copy them back verbatim. Station and depot orders reuse bit 2/3 for different meanings. */
	function OrderFlagNames(flags, is_depot) {
		local out = [];
		if (flags == GSOrder.OF_INVALID) return out;
		if (flags & GSOrder.OF_NON_STOP_INTERMEDIATE) out.append("non_stop");
		if (flags & GSOrder.OF_NON_STOP_DESTINATION) out.append("via");
		if (is_depot) {
			if (flags & GSOrder.OF_SERVICE_IF_NEEDED) out.append("service");
			if (flags & GSOrder.OF_STOP_IN_DEPOT) out.append("stop_in_depot");
			if (flags & GSOrder.OF_GOTO_NEAREST_DEPOT) out.append("go_depot");
			return out;
		}
		if (flags & GSOrder.OF_UNLOAD) out.append("unload");
		if (flags & GSOrder.OF_TRANSFER) out.append("transfer");
		if (flags & GSOrder.OF_NO_UNLOAD) out.append("no_unload");
		local load = flags & (GSOrder.OF_FULL_LOAD | GSOrder.OF_FULL_LOAD_ANY);
		if (load == GSOrder.OF_FULL_LOAD_ANY) out.append("full_load_any");
		else if (load == GSOrder.OF_FULL_LOAD) out.append("full_load");
		if (flags & GSOrder.OF_NO_LOAD) out.append("no_load");
		return out;
	}

	/* Per-cargo capacity/load of a vehicle (all wagons) for every cargo it can hold. */
	function VehicleCargo(v) {
		local out = [];
		foreach (c, _ in GSCargoList()) {
			local cap = GSVehicle.GetCapacity(v, c);
			if (cap > 0) out.append({ cargo = c, capacity = cap, load = GSVehicle.GetCargoLoad(v, c) });
		}
		return out;
	}

	function VehicleRow(v) {
		local tile = GSVehicle.GetLocation(v);
		local vt = GSVehicle.GetVehicleType(v);
		local eng = GSVehicle.GetEngineType(v);
		local state = GSVehicle.GetState(v);
		local r = QH.Located(v, tile);
		r.name <- GSVehicle.GetName(v);
		r.unit <- GSVehicle.GetUnitNumber(v);
		r.vtype <- QH.VTName(vt);
		r.engine <- eng;
		r.engine_name <- GSEngine.GetName(eng);
		r.state <- (state >= 0 && state < QH.VEHICLE_STATES.len()) ? QH.VEHICLE_STATES[state] : "invalid";
		r.speed <- GSVehicle.GetCurrentSpeed(v);
		r.age <- GSVehicle.GetAge(v);
		r.max_age <- GSVehicle.GetMaxAge(v);
		r.reliability <- GSVehicle.GetReliability(v);
		r.profit_this_year <- GSVehicle.GetProfitThisYear(v);
		r.profit_last_year <- GSVehicle.GetProfitLastYear(v);
		r.value <- GSVehicle.GetCurrentValue(v);
		r.running_cost <- GSVehicle.GetRunningCost(v);
		/* Main cargo = largest capacity; the full split only when the vehicle mixes cargos. */
		local cargos = QH.VehicleCargo(v);
		local main = null;
		foreach (c in cargos) if (main == null || c.capacity > main.capacity) main = c;
		r.cargo <- (main == null) ? -1 : main.cargo;
		r.capacity <- (main == null) ? 0 : main.capacity;
		r.load <- (main == null) ? 0 : main.load;
		if (cargos.len() > 1) r.cargos <- cargos;
		r.orders <- GSOrder.GetOrderCount(v);
		r.cur_order <- GSOrder.ResolveOrderPosition(v, GSOrder.ORDER_CURRENT);
		r.in_depot <- GSVehicle.IsInDepot(v);
		r.stopped <- GSVehicle.IsStoppedInDepot(v) || state == GSVehicle.VS_STOPPED;
		if (vt == GSVehicle.VT_RAIL) r.wagons <- GSVehicle.GetNumWagons(v);
		return r;
	}

	function SubsidyEnd(type, idx) {
		if (type == GSSubsidy.SPT_TOWN && GSTown.IsValidTown(idx)) {
			local e = QH.Located(idx, GSTown.GetLocation(idx));
			e.type <- "town";
			e.name <- GSTown.GetName(idx);
			return e;
		}
		if (type == GSSubsidy.SPT_INDUSTRY && GSIndustry.IsValidIndustry(idx)) {
			local e = QH.Located(idx, GSIndustry.GetLocation(idx));
			e.type <- "industry";
			e.name <- GSIndustry.GetName(idx);
			return e;
		}
		return { type = "invalid", id = idx, name = null, tile = -1, x = -1, y = -1 };
	}

	function EngineRow(e, vt) {
		local r = {
			id = e, name = GSEngine.GetName(e), vtype = QH.VTName(vt), cargo = QH.Cargo(GSEngine.GetCargoType(e)),
			capacity = GSEngine.GetCapacity(e), speed = GSEngine.GetMaxSpeed(e), price = GSEngine.GetPrice(e),
			running_cost = GSEngine.GetRunningCost(e), reliability = GSEngine.GetReliability(e),
			max_age = GSEngine.GetMaxAge(e), wagon = GSEngine.IsWagon(e), articulated = GSEngine.IsArticulated(e)
		};
		if (vt == GSVehicle.VT_RAIL || vt == GSVehicle.VT_ROAD) {
			r.power <- GSEngine.GetPower(e);
			r.weight <- GSEngine.GetWeight(e);
		}
		if (vt == GSVehicle.VT_RAIL) {
			r.rail_type <- GSEngine.GetRailType(e);
			r.rail_type_name <- GSRail.GetName(r.rail_type);
		} else if (vt == GSVehicle.VT_ROAD) {
			r.road_type <- GSEngine.GetRoadType(e);
			r.road_type_name <- GSRoad.GetName(r.road_type);
		} else if (vt == GSVehicle.VT_AIR) {
			local pt = GSEngine.GetPlaneType(e);
			r.plane_type <- (pt in QH.PLANE_TYPES) ? QH.PLANE_TYPES[pt] : "invalid";
			r.range <- GSEngine.GetMaximumOrderDistance(e);
		}
		return r;
	}
}

Query <- {
	/* ------------------------------------------------------------------ world */
	function H_map_info(br, id, a) {
		local v = QH.DateTable(GSDate.GetCurrentDate());
		v.size_x <- GSMap.GetMapSizeX();
		v.size_y <- GSMap.GetMapSizeY();
		v.climate <- QH.Climate();
		v.company <- br.company;
		v.company_name <- (br.company >= 0) ? GSCompany.GetName(br.company) : null;
		v.ticks <- br.ticks;
		v.towns <- GSTown.GetTownCount();
		v.industries <- GSIndustry.GetIndustryCount();
		Proto.Ok(id, v);
	}

	function H_cargos(br, id, a) {
		local rows = [];
		local cl = GSCargoList();
		cl.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
		foreach (c, _ in cl) {
			rows.append({
				id = c, label = GSCargo.GetCargoLabel(c), name = GSCargo.GetName(c),
				freight = GSCargo.IsFreight(c), town_effect = QH.TownEffect(c)
			});
		}
		Proto.Rows(id, rows);
	}

	/* Towns. Args: near_tile, radius. pax_produced = last-month passengers generated (the town
	 * window's "max"), pax_transported = moved to stations, pax_pct = transported share; mail alike.
	 * rating is the bound company's local-authority rating (none until it acts in the town). */
	function H_towns(br, id, a) {
		local pax = QH.CargoWithEffect(GSCargo.TE_PASSENGERS);
		local mail = QH.CargoWithEffect(GSCargo.TE_MAIL);
		local rows = [];
		local tl = GSTownList();
		tl.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
		foreach (t, _ in tl) {
			local r = QH.Located(t, GSTown.GetLocation(t));
			r.name <- GSTown.GetName(t);
			r.population <- GSTown.GetPopulation(t);
			r.houses <- GSTown.GetHouseCount(t);
			r.city <- GSTown.IsCity(t);
			r.rating <- QH.TownRating(t, br.company);
			r.rating_pts <- (r.rating == "none") ? null : GSTown.GetDetailedRating(t, br.company);
			if (pax >= 0) {
				r.pax_produced <- GSTown.GetLastMonthProduction(t, pax);
				r.pax_transported <- GSTown.GetLastMonthSupplied(t, pax);
				r.pax_pct <- GSTown.GetLastMonthTransportedPercentage(t, pax);
			}
			if (mail >= 0) {
				r.mail_produced <- GSTown.GetLastMonthProduction(t, mail);
				r.mail_transported <- GSTown.GetLastMonthSupplied(t, mail);
				r.mail_pct <- GSTown.GetLastMonthTransportedPercentage(t, mail);
			}
			rows.append(r);
		}
		Proto.Rows(id, QH.ApplyNear(rows, QH.Near(a)));
	}

	/* Industries. Args: cargo (+ role produces|accepts|any), near_tile, radius.
	 * produces[] = {cargo, last_month, transported, pct}; accepts[] = cargo ids;
	 * stations_around counts every company's stations whose catchment covers the industry. */
	function H_industries(br, id, a) {
		local want = QH.Int(a, "cargo", -1);
		local role = ("role" in a) ? a.role : "any";
		if (role != "any" && role != "produces" && role != "accepts") {
			return Proto.Err(id, "ERR_BAD_ARG", "role must be produces, accepts or any");
		}
		local rows = [];
		local il = GSIndustryList();
		il.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
		foreach (i, _ in il) {
			local typ = GSIndustry.GetIndustryType(i);
			local produced = GSIndustryType.GetProducedCargo(typ);
			local accepted = GSIndustryType.GetAcceptedCargo(typ);
			if (want >= 0) {
				local p = produced.HasItem(want), c = accepted.HasItem(want);
				if (role == "produces" ? !p : role == "accepts" ? !c : !(p || c)) continue;
			}
			local produces = [];
			foreach (c, _ in produced) {
				produces.append({ cargo = c, last_month = GSIndustry.GetLastMonthProduction(i, c),
					transported = GSIndustry.GetLastMonthTransported(i, c),
					pct = GSIndustry.GetLastMonthTransportedPercentage(i, c) });
			}
			local accepts = [];
			foreach (c, _ in accepted) accepts.append(c);
			local r = QH.Located(i, GSIndustry.GetLocation(i));
			r.name <- GSIndustry.GetName(i);
			r.type <- typ;
			r.type_name <- GSIndustryType.GetName(typ);
			r.produces <- produces;
			r.accepts <- accepts;
			r.stations_around <- GSIndustry.GetAmountOfStationsAround(i);
			rows.append(r);
		}
		Proto.Rows(id, QH.ApplyNear(rows, QH.Near(a)));
	}

	/* ------------------------------------------------------------------ land */

	/* Compact map strip. Args x, y, w, h (default 64x64), clipped to the map; clipped w*h <= 4096.
	 * One row per y: {y, x (first column), kind, height (base-36 min corner height), owner
	 * ('*' bound company, 'o' another company, '.' town/none)}; alphabet in QH.Kind. */
	function H_tiles(br, id, a) {
		local sx = GSMap.GetMapSizeX(), sy = GSMap.GetMapSizeY();
		local x0 = QH.Int(a, "x", 0), y0 = QH.Int(a, "y", 0);
		local w = QH.Int(a, "w", 64), h = QH.Int(a, "h", 64);
		if (x0 < 0 || y0 < 0 || x0 >= sx || y0 >= sy || w < 1 || h < 1) {
			return Proto.Err(id, "ERR_BAD_ARG", "need 0<=x<" + sx + ", 0<=y<" + sy + ", w>=1, h>=1");
		}
		local x1 = (x0 + w > sx) ? sx : x0 + w;
		local y1 = (y0 + h > sy) ? sy : y0 + h;
		if ((x1 - x0) * (y1 - y0) > QH.MAX_TILES) {
			return Proto.Err(id, "ERR_BAD_ARG", "area " + (x1 - x0) + "x" + (y1 - y0) + " exceeds " + QH.MAX_TILES + " tiles; request strips");
		}
		local freeform = QH.FreeformEdges();
		local rows = [];
		for (local y = y0; y < y1; y++) {
			local kind = "", height = "", owner = "";
			for (local x = x0; x < x1; x++) {
				if (QH.IsVoid(x, y, freeform)) {
					kind += " ";
					height += "0";
					owner += ".";
					continue;
				}
				local t = y * sx + x;
				kind += QH.Kind(t);
				height += QH.HeightChar(GSTile.GetMinHeight(t));
				owner += QH.OwnerChar(t, br.company);
			}
			rows.append({ y = y, x = x0, kind = kind, height = height, owner = owner });
		}
		Proto.Rows(id, rows);
	}

	/* Everything a model needs to judge one tile for building. Args: tile | x,y; radius (default 3)
	 * for the catchment summary (accepts/produces/industries as in H_catchment). */
	function H_tile(br, id, a) {
		local t = QH.TileArg(a);
		if (t == null) return Proto.Err(id, "ERR_BAD_ARG", "tile (index) or x,y on the map required");
		local radius = QH.Int(a, "radius", 3);
		if (radius < 0 || radius > 12) return Proto.Err(id, "ERR_BAD_ARG", "radius must be 0..12");
		local x = GSMap.GetTileX(t), y = GSMap.GetTileY(t);
		local owner = GSTile.GetOwner(t);
		local ind = GSIndustry.GetIndustryID(t);
		local auth = GSTile.GetTownAuthority(t);
		local near = GSTile.GetClosestTown(t);
		local terrain = GSTile.GetTerrainType(t);
		local v = {
			tile = t, x = x, y = y,
			kind = QH.IsVoid(x, y, QH.FreeformEdges()) ? " " : QH.Kind(t),
			height = GSTile.GetMinHeight(t), max_height = GSTile.GetMaxHeight(t), slope = GSTile.GetSlope(t),
			buildable = GSTile.IsBuildable(t),
			terrain = (terrain >= 0 && terrain < QH.TERRAINS.len()) ? QH.TERRAINS[terrain] : "normal",
			water = GSTile.IsWaterTile(t), coast = GSTile.IsCoastTile(t), river = GSTile.IsRiverTile(t),
			trees = GSTile.HasTreeOnTile(t), house = GSTile.IsHouseTile(t),
			road = GSRoad.IsRoadTile(t), rail = GSRail.IsRailTile(t),
			station = GSTile.IsStationTile(t) ? GSStation.GetStationID(t) : -1,
			depot = GSRail.IsRailDepotTile(t) || GSRoad.IsRoadDepotTile(t) || GSMarine.IsWaterDepotTile(t) || GSAirport.IsHangarTile(t),
			industry = GSIndustry.IsValidIndustry(ind) ? ind : -1,
			town = GSTown.IsValidTown(auth) ? auth : -1,
			closest_town = GSTown.IsValidTown(near)
				? { id = near, name = GSTown.GetName(near), dist = GSTown.GetDistanceManhattanToTile(near, t) } : null,
			owner = owner, mine = owner >= 0 && owner == br.company,
			radius = radius
		};
		if (v.rail) v.rail_type <- GSRail.GetRailType(t);
		local c = QH.Catchment(t, 1, 1, radius);
		v.accepts <- c.accepts;
		v.produces <- c.produces;
		v.industries <- c.industries;
		Proto.Ok(id, v);
	}

	/* Station-placement check. Args: tile | x,y; w, h (station footprint, default 1x1, max 16);
	 * radius (catchment, default 3, max 12). Returns accepts[{cargo,label,amount,accepted}]
	 * (amount in 1/8: >= 8 is accepted), produces[{cargo,label,producers}], industries[{id,name}]. */
	function H_catchment(br, id, a) {
		local t = QH.TileArg(a);
		if (t == null) return Proto.Err(id, "ERR_BAD_ARG", "tile (index) or x,y on the map required");
		local w = QH.Int(a, "w", 1), h = QH.Int(a, "h", 1), radius = QH.Int(a, "radius", 3);
		if (w < 1 || h < 1 || w > 16 || h > 16 || radius < 0 || radius > 12) {
			return Proto.Err(id, "ERR_BAD_ARG", "need 1<=w,h<=16 and 0<=radius<=12");
		}
		local c = QH.Catchment(t, w, h, radius);
		c.tile <- t;
		c.x <- GSMap.GetTileX(t);
		c.y <- GSMap.GetTileY(t);
		c.w <- w;
		c.h <- h;
		c.radius <- radius;
		Proto.Ok(id, c);
	}

	/* Offered and awarded subsidies. Rows: {id, cargo, label, awarded, awarded_to (-1), mine,
	 * expires {date,year,month,day} (offer deadline, or end of the award), from/to {type
	 * town|industry, id, name, tile, x, y}}. */
	function H_subsidies(br, id, a) {
		local rows = [];
		local sl = GSSubsidyList();
		sl.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
		foreach (s, _ in sl) {
			local c = GSSubsidy.GetCargoType(s);
			local awarded = GSSubsidy.IsAwarded(s);
			local to = awarded ? GSSubsidy.GetAwardedTo(s) : -1;
			rows.append({
				id = s, cargo = c, label = GSCargo.GetCargoLabel(c), awarded = awarded,
				awarded_to = to, mine = awarded && to == br.company,
				expires = QH.DateTable(GSSubsidy.GetExpireDate(s)),
				from = QH.SubsidyEnd(GSSubsidy.GetSourceType(s), GSSubsidy.GetSourceIndex(s)),
				to = QH.SubsidyEnd(GSSubsidy.GetDestinationType(s), GSSubsidy.GetDestinationIndex(s))
			});
		}
		Proto.Rows(id, rows);
	}

	/* ------------------------------------------------------------------ company-specific */

	/* Engines the bound company can build. Args: vtype (required), cargo (optional: keep engines that
	 * carry it natively or by refit — `carries`=true — plus rail locomotives able to pull it). */
	function H_engines(br, id, a) {
		local vt = QH.VType(a);
		if (vt == null) return Proto.Err(id, "ERR_BAD_ARG", "vtype must be rail, road, water or air");
		if (QH.NoCompany(br, id)) return;
		local want = QH.Int(a, "cargo", -1);
		local cm = GSCompanyMode(br.company);
		local rows = [];
		local el = GSEngineList(vt);
		el.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
		foreach (e, _ in el) {
			if (!GSEngine.IsBuildable(e)) continue;
			local carries = null;
			if (want >= 0) {
				carries = GSEngine.GetCargoType(e) == want || GSEngine.CanRefitCargo(e, want);
				if (!carries && (GSEngine.IsWagon(e) || !GSEngine.CanPullCargo(e, want))) continue;
			}
			local r = QH.EngineRow(e, vt);
			if (carries != null) r.carries <- carries;
			rows.append(r);
		}
		Proto.Rows(id, rows);
	}

	/* The bound company's stations. Args: near_tile, radius. types[] ⊆ train|truck|bus|airport|dock;
	 * cargo[] = {cargo, waiting, rating (percent)} for every cargo with a rating or waiting cargo;
	 * vehicles = vehicles with orders to the station; town = nearest town. */
	function H_stations(br, id, a) {
		if (QH.NoCompany(br, id)) return;
		local cm = GSCompanyMode(br.company);
		local cargos = GSCargoList();
		local rows = [];
		local sl = GSStationList(GSStation.STATION_ANY);
		sl.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
		foreach (st, _ in sl) {
			local cargo = [];
			foreach (c, _ in cargos) {
				local waiting = GSStation.GetCargoWaiting(st, c);
				local rated = GSStation.HasCargoRating(st, c);
				if (!rated && waiting <= 0) continue;
				cargo.append({ cargo = c, waiting = waiting, rating = rated ? GSStation.GetCargoRating(st, c) : null });
			}
			local r = QH.Located(st, GSStation.GetLocation(st));
			r.name <- GSStation.GetName(st);
			r.types <- QH.StationTypes(st);
			r.cargo <- cargo;
			r.vehicles <- GSVehicleList_Station(st).Count();
			r.town <- GSStation.GetNearestTown(st);
			r.radius <- GSStation.GetStationCoverageRadius(st);
			rows.append(r);
		}
		Proto.Rows(id, QH.ApplyNear(rows, QH.Near(a)));
	}

	/* The bound company's vehicles (primary vehicles only). Args: vtype, near_tile, radius.
	 * cargo/capacity/load describe the main (largest-capacity) cargo; `cargos` lists all when mixed.
	 * cur_order is the index into orders (-1 when the current order is not in the list, e.g. servicing). */
	function H_vehicles(br, id, a) {
		local vt = null;
		if ("vtype" in a) {
			vt = QH.VType(a);
			if (vt == null) return Proto.Err(id, "ERR_BAD_ARG", "vtype must be rail, road, water or air");
		}
		if (QH.NoCompany(br, id)) return;
		local cm = GSCompanyMode(br.company);
		local rows = [];
		local vl = GSVehicleList();
		vl.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
		foreach (v, _ in vl) {
			if (!GSVehicle.IsPrimaryVehicle(v)) continue;
			if (vt != null && GSVehicle.GetVehicleType(v) != vt) continue;
			rows.append(QH.VehicleRow(v));
		}
		Proto.Rows(id, QH.ApplyNear(rows, QH.Near(a)));
	}

	/* Orders of one of the bound company's vehicles. Args: vehicle. Rows: {i, kind
	 * (station|depot|waypoint|conditional|void|other), tile/x/y (-1 when unknown, e.g. nearest depot),
	 * station (id or -1), flags[] (order.append vocabulary), current, jump_to (conditional), refit}. */
	function H_orders(br, id, a) {
		if (!("vehicle" in a) || typeof a.vehicle != "integer") return Proto.Err(id, "ERR_BAD_ARG", "vehicle (id) required");
		if (QH.NoCompany(br, id)) return;
		local cm = GSCompanyMode(br.company);
		local v = a.vehicle;
		if (!GSVehicle.IsValidVehicle(v) || !GSVehicle.IsPrimaryVehicle(v)) {
			return Proto.Err(id, "ERR_NO_VEHICLE", "vehicle " + v + " is not one of your vehicles (see vehicles)");
		}
		local cur = GSOrder.ResolveOrderPosition(v, GSOrder.ORDER_CURRENT);
		local rows = [];
		local n = GSOrder.GetOrderCount(v);
		for (local i = 0; i < n; i++) {
			local kind = GSOrder.IsGotoStationOrder(v, i) ? "station"
				: GSOrder.IsGotoDepotOrder(v, i) ? "depot"
				: GSOrder.IsGotoWaypointOrder(v, i) ? "waypoint"
				: GSOrder.IsConditionalOrder(v, i) ? "conditional"
				: GSOrder.IsVoidOrder(v, i) ? "void" : "other";
			local dest = GSOrder.GetOrderDestination(v, i);
			local ok = GSMap.IsValidTile(dest);
			local r = {
				i = i, kind = kind, tile = ok ? dest : -1, x = ok ? GSMap.GetTileX(dest) : -1, y = ok ? GSMap.GetTileY(dest) : -1,
				station = (ok && GSTile.IsStationTile(dest)) ? GSStation.GetStationID(dest) : -1,
				flags = QH.OrderFlagNames(GSOrder.GetOrderFlags(v, i), kind == "depot"),
				current = i == cur
			};
			if (kind == "conditional") r.jump_to <- GSOrder.GetOrderJumpTo(v, i);
			if (GSOrder.IsRefitOrder(v, i)) r.refit <- QH.Cargo(GSOrder.GetOrderRefit(v, i));
			rows.append(r);
		}
		Proto.Rows(id, rows);
	}

	/* The bound company's depots (hangars for air). Args: vtype (optional: all types when absent).
	 * Rows: {tile, x, y, vtype, front (entry tile, -1 for hangars/ship depots), vehicles (inside),
	 * rail_type (rail only)}. */
	function H_depots(br, id, a) {
		local types = [0, 1, 2, 3];
		if ("vtype" in a) {
			local vt = QH.VType(a);
			if (vt == null) return Proto.Err(id, "ERR_BAD_ARG", "vtype must be rail, road, water or air");
			types = [vt];
		}
		if (QH.NoCompany(br, id)) return;
		local cm = GSCompanyMode(br.company);
		local rows = [];
		foreach (vt in types) {
			local dl = GSDepotList(QH.DepotTransport(vt));
			dl.Sort(GSList.SORT_BY_ITEM, GSList.SORT_ASCENDING);
			foreach (t, _ in dl) {
				local front = -1;
				if (vt == GSVehicle.VT_RAIL) front = GSRail.GetRailDepotFrontTile(t);
				else if (vt == GSVehicle.VT_ROAD) front = GSRoad.GetRoadDepotFrontTile(t);
				local r = { tile = t, x = GSMap.GetTileX(t), y = GSMap.GetTileY(t), vtype = QH.VTName(vt),
					front = GSMap.IsValidTile(front) ? front : -1, vehicles = GSVehicleList_Depot(t).Count() };
				if (vt == GSVehicle.VT_RAIL) r.rail_type <- GSRail.GetRailType(t);
				rows.append(r);
			}
		}
		Proto.Rows(id, rows);
	}

	/* Finances of the bound company. quarters[q]: q=0 is the running quarter (rating null: it is only
	 * computed at quarter end), q=1..4 the last four completed economy-quarters. */
	function H_company(br, id, a) {
		if (QH.NoCompany(br, id)) return;
		local c = br.company;
		local cm = GSCompanyMode(c);
		local quarters = [];
		for (local q = 0; q <= 4; q++) {
			quarters.append({
				q = q, income = GSCompany.GetQuarterlyIncome(c, q), expenses = GSCompany.GetQuarterlyExpenses(c, q),
				delivered = GSCompany.GetQuarterlyCargoDelivered(c, q),
				rating = (q == 0) ? null : GSCompany.GetQuarterlyPerformanceRating(c, q),
				value = GSCompany.GetQuarterlyCompanyValue(c, q)
			});
		}
		local hq = GSCompany.GetCompanyHQ(c);
		Proto.Ok(id, {
			id = c, name = GSCompany.GetName(c), money = GSCompany.GetBankBalance(c),
			loan = GSCompany.GetLoanAmount(), max_loan = GSCompany.GetMaxLoanAmount(),
			loan_interval = GSCompany.GetLoanInterval(),
			value = GSCompany.GetQuarterlyCompanyValue(c, GSCompany.CURRENT_QUARTER),
			hq = GSMap.IsValidTile(hq) ? hq : -1, quarters = quarters
		});
	}
}
