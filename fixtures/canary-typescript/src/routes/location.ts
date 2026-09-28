import { Router } from "express";
import { fetchForecast } from "../weather.js";

export const locationRouter = Router();

locationRouter.post("/location/forecast", async (req, res) => {
  const { latitude, longitude, units = "metric" } = req.body;
  res.json(await fetchForecast(latitude, longitude, units));
});
