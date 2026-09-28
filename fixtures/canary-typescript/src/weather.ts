import axios from "axios";
import { config } from "./config.js";

export async function fetchForecast(
  lat: number,
  lng: number,
  units: string,
): Promise<Record<string, unknown>> {
  const response = await axios.get(config.weatherApiUrl, {
    params: { lat, lon: lng, units },
    timeout: 5000,
  });
  return response.data as Record<string, unknown>;
}
