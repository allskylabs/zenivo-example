// Build-time stub: the sky page does not propagate TLEs (the backend does), so the engine's satellite helpers are never called.
const no = () => { throw new Error('satellite.js is stubbed out in skyview'); };
export const ecfToLookAngles = no, geodeticToEcf = no, gstime = no, sgp4 = no, twoline2satrec = no;
