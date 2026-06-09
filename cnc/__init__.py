"""Automated CNC machining quote system.

Pipeline:  [前端] -> [几何解析] -> [工艺/工时规划] -> [成本/利润] -> [报价单]

    cnc.geometry  — parse STEP/STL, bounding box, volume, area, features
    cnc.engine    — CAPP time planning + costing model + shop reference data
    cnc.service   — orchestrates the funnel into one quote payload
    cnc.quote     — render the payload to a PDF quotation
    cnc.api       — FastAPI app (upload, quote, PDF) + Three.js frontend
"""
__all__ = ["geometry", "engine", "service", "quote", "api"]
