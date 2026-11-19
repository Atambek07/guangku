package com.guangku.plotter

import android.content.Context
import android.content.res.AssetManager
import org.nanohttpd.protocols.http.IHTTPSession
import org.nanohttpd.protocols.http.NanoHTTPD
import org.nanohttpd.protocols.http.response.Response
import org.nanohttpd.protocols.http.response.Status
import org.nanohttpd.protocols.http.response.StringResponse
import java.io.File
import java.io.InputStream

/**
 * Встроенный веб-сервер на телефоне. Отдаёт тот же HTML/JS интерфейс,
 * что и на ноутбуке, но команды G-code уходят напрямую в USB-плоттер через UsbPlotter.
 */
class WebServer(
    private val context: Context,
    private val plotter: UsbPlotter,
    port: Int = 8000
) : NanoHTTPD(port) {

    private val assets: AssetManager = context.assets

    override fun serve(session: IHTTPSession): Response {
        val uri = session.uri
        val method = session.method

        return when {
            uri == "/" || uri == "/index.html" -> serveAsset("index.html", "text/html")
            uri.startsWith("/static/") -> serveStatic(uri.substring("/static/".length))
            uri == "/api/serial/status" -> apiSerialStatus()
            uri == "/api/serial/connect" -> apiSerialConnect()
            uri == "/api/serial/disconnect" -> apiSerialDisconnect()
            uri == "/api/serial/ports" -> apiSerialPorts()
            uri == "/api/serial/stream" -> apiSerialStream(session)
            uri == "/api/serial/pause" -> apiCmd(plotter::pause)
            uri == "/api/serial/resume" -> apiCmd(plotter::resume)
            uri == "/api/serial/stop" -> apiCmd(plotter::stop)
            uri == "/api/serial/home" -> apiCmd(plotter::home)
            uri == "/api/serial/zero" -> apiCmd(plotter::zero)
            uri == "/api/serial/pen_down" -> apiCmd(plotter::penDown)
            uri == "/api/serial/pen_up" -> apiCmd(plotter::penUp)
            uri.startsWith("/api/serial/jog") -> apiJog(session)
            uri == "/api/fonts" -> apiFonts()
            uri == "/api/presets" -> apiPresets()
            uri == "/api/layout" -> apiLayout(session)
            uri == "/api/gcode" -> apiGcode(session)
            else -> Response(Status.NOT_FOUND, "text/plain", "Not found: $uri")
        }
    }

    // ---------- статика ----------
    private fun serveAsset(name: String, mime: String): Response {
        return try {
            val is: InputStream = assets.open("www/$name")
            val content = is.bufferedReader().use { it.readText() }
            Response(Status.OK, mime, content)
        } catch (e: Exception) {
            Response(Status.NOT_FOUND, "text/plain", "Asset not found: $name")
        }
    }

    private fun serveStatic(path: String): Response {
        val mime = when {
            path.endsWith(".css") -> "text/css"
            path.endsWith(".js") -> "application/javascript"
            path.endsWith(".svg") -> "image/svg+xml"
            path.endsWith(".png") -> "image/png"
            else -> "text/plain"
        }
        return try {
            val is: InputStream = assets.open("www/$path")
            Response(Status.OK, mime, is)
        } catch (e: Exception) {
            Response(Status.NOT_FOUND, "text/plain", "Static not found: $path")
        }
    }

    // ---------- JSON helpers ----------
    private fun json(map: Map<String, Any?>): Response {
        val sb = StringBuilder("{")
        map.entries.joinToString(sb, prefix = "", postfix = "}", transform = { (k, v) ->
            "\"$k\":${jsonValue(v)}"
        })
        return Response(Status.OK, "application/json", sb.toString())
    }

    private fun jsonValue(v: Any?): String = when (v) {
        null -> "null"
        is String -> "\"${v.replace("\"", "\\\"")}\""
        else -> v.toString()
    }

    private fun ok(message: String? = null): Response {
        return json(mapOf("ok" to true) + (if (message != null) mapOf("message" to message) else emptyMap()))
    }

    private fun err(msg: String): Response {
        return json(mapOf("ok" to false, "error" to msg))
    }

    // ---------- API endpoints ----------
    private fun apiSerialStatus(): Response {
        return json(mapOf(
            "state" to if (plotter.isConnected()) "connected" else "disconnected",
            "progress" to 0,
            "message" to if (plotter.isConnected()) "Плоттер подключён через USB-OTG" else "Плоттер не подключён"
        ))
    }

    private fun apiSerialConnect(): Response {
        val result = plotter.connect()
        return if (result) ok("Подключён к плоттеру") else err("Не удалось подключиться к USB-устройству")
    }

    private fun apiSerialDisconnect(): Response {
        plotter.disconnect()
        return ok("Отключён")
    }

    private fun apiSerialPorts(): Response {
        // На Android список портов получается через UsbManager
        val usbManager = context.getSystemService(Context.USB_SERVICE) as android.hardware.usb.UsbManager
        val devices = usbManager.deviceList.values
        val ports = devices.map { d ->
            mapOf(
                "device" to d.deviceName,
                "description" to "${d.manufacturerName ?: ""} ${d.productName ?: ""}".trim(),
                "vendorId" to d.vendorId,
                "productId" to d.productId
            )
        }
        return json(mapOf("ports" to ports))
    }

    private fun apiSerialStream(session: IHTTPSession): Response {
        // Читаем тело POST — это G-code
        val body = session.getPostParameter("gcode") ?: session.getPostParameter("data") ?: ""
        val lines = body.lines().filter { it.isNotEmpty() && !it.startsWith(";") }
        for (line in lines) {
            plotter.send(line)
        }
        return ok("Отправлено ${lines.size} команд на плоттер (через USB-OTG)")
    }

    private fun apiCmd(action: () -> Unit): Response {
        try {
            action()
            return ok()
        } catch (e: Exception) {
            return err(e.message ?: "Ошибка команды")
        }
    }

    private fun apiJog(session: IHTTPSession): Response {
        val params = session.queryParameterDictionary
        val axis = params.getOrDefault("axis", "X")
        val mm = params.getOrDefault("mm", "10").toDoubleOrNull() ?: 10.0
        plotter.jog(axis, mm)
        return ok("Жог $axis $mm мм")
    }

    private fun apiFonts(): Response {
        // На телефоне шрифты лежат в assets/www/fonts/
        return json(mapOf(
            "fonts" to listOf(mapOf(
                "name" to "Eskal",
                "filename" to "Eskal.ttf",
                "cyrillic_present" to 91,
                "cyrillic_total" to 256,
                "cyrillic_coverage" to 35.5,
                "sample_ok" to false,
                "missing_sample" to listOf("Ъ"),
                "error" to ""
            )),
            "default" to "Eskal"
        ))
    }

    private fun apiPresets(): Response {
        val presets = listOf(
            mapOf(
                "id" to "notebook_grid_5mm", "name" to "Тетрадь в клетку (5×5 мм)", "kind" to "grid",
                "work_width_mm" to 210.0, "work_height_mm" to 300.0,
                "margin_left_mm" to 8.0, "margin_right_mm" to 8.0,
                "margin_top_mm" to 8.0, "margin_bottom_mm" to 8.0,
                "cell_size_mm" to 5.0, "line_spacing_mm" to 8.0,
                "font_size_mm" to 6.0, "line_height_mm" to 5.0,
                "tracking_mm" to 0.8, "slant_deg" to 0.0,
                "red_line_indent_mm" to 5.0, "font_name" to ""
            ),
            mapOf(
                "id" to "notebook_lined", "name" to "Тетрадь в линейку", "kind" to "lined",
                "work_width_mm" to 210.0, "work_height_mm" to 300.0,
                "margin_left_mm" to 10.0, "margin_right_mm" to 10.0,
                "margin_top_mm" to 12.0, "margin_bottom_mm" to 12.0,
                "line_spacing_mm" to 10.0, "font_size_mm" to 7.0,
                "line_height_mm" to 10.0, "tracking_mm" to 1.0,
                "slant_deg" to 0.0, "red_line_indent_mm" to 8.0, "font_name" to ""
            ),
            mapOf(
                "id" to "notebook_blank", "name" to "Чистый лист тетради", "kind" to "blank",
                "work_width_mm" to 210.0, "work_height_mm" to 300.0,
                "margin_left_mm" to 10.0, "margin_right_mm" to 10.0,
                "margin_top_mm" to 12.0, "margin_bottom_mm" to 12.0,
                "font_size_mm" to 8.0, "line_height_mm" to 14.0,
                "tracking_mm" to 1.0, "slant_deg" to 0.0,
                "red_line_indent_mm" to 10.0, "font_name" to ""
            ),
            mapOf(
                "id" to "a4", "name" to "Лист A4 (210×297 мм)", "kind" to "a4",
                "work_width_mm" to 210.0, "work_height_mm" to 297.0,
                "margin_left_mm" to 15.0, "margin_right_mm" to 15.0,
                "margin_top_mm" to 15.0, "margin_bottom_mm" to 15.0,
                "font_size_mm" to 10.0, "line_height_mm" to 15.0,
                "tracking_mm" to 1.2, "slant_deg" to 0.0,
                "red_line_indent_mm" to 10.0, "font_name" to ""
            )
        )
        return json(mapOf("presets" to presets))
    }

    private fun apiLayout(session: IHTTPSession): Response {
        // Упрощённая версия: принимаем JSON с текстом и возвращаем заготовку
        // Полноценный конвертер шрифтов+разметки можно портировать из backend/layout.py
        val body = session.getPostParameter("text") ?: ""
        return json(mapOf(
            "page_count" to 1,
            "total_chars" to body.length,
            "total_strokes" to 0,
            "est_time_min" to 0.0,
            "preview_svg" to "<svg xmlns='http://www.w3.org/2000/svg' width='210' height='300'><text x='10' y='20'>Конспект на телефоне</text></svg>",
            "pages" to listOf(
                mapOf(
                    "page_no" to 1,
                    "width_mm" to 210.0,
                    "height_mm" to 300.0,
                    "strokes" to emptyList<Map<String, Any>>()
                )
            )
        ))
    }

    private fun apiGcode(session: IHTTPSession): Response {
        // Принимает страницы, возвращает G-code (упрощённо)
        return json(mapOf(
            "ok" to true,
            "filename" to "phone_job.nc",
            "line_count" to 0,
            "est_time_min" to 0.0,
            "mm_total" to 0.0,
            "gcode" to "; G-code сгенерирован на телефоне\nG21\nG90\nM2\n"
        ))
    }
}
