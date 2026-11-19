package com.guangku.plotter

import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private lateinit var plotter: UsbPlotter
    private lateinit var webServer: WebServer

    private lateinit var tvStatus: TextView
    private lateinit var tvLog: TextView
    private lateinit var btnConnect: Button
    private lateinit var btnDisconnect: Button
    private lateinit var btnOpenWeb: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tvStatus = findViewById(R.id.tvStatus)
        tvLog = findViewById(R.id.tvLog)
        btnConnect = findViewById(R.id.btnConnect)
        btnDisconnect = findViewById(R.id.btnDisconnect)
        btnOpenWeb = findViewById(R.id.btnOpenWeb)

        plotter = UsbPlotter(this)
        plotter.setListener { msg -> log(msg) }

        // Запускаем встроенный веб-сервер на порту 8000
        webServer = WebServer(this, plotter, 8000)
        try {
            webServer.start()
            log("Веб-сервер запущен: http://127.0.0.1:8000")
        } catch (e: Exception) {
            log("Ошибка веб-сервера: ${e.message}")
        }

        btnConnect.setOnClickListener {
            log("Подключение к плоттеру…")
            tvStatus.text = "Подключение…"
            val ok = plotter.connect()
            if (ok) {
                tvStatus.text = "Подключено к плоттеру"
                btnConnect.isEnabled = false
                btnDisconnect.isEnabled = true
                log("Плоттер готов к работе через USB-OTG")
            } else {
                tvStatus.text = "Не удалось подключиться"
                log("Ошибка: проверьте OTG-кабель и разрешение на USB")
            }
        }

        btnDisconnect.setOnClickListener {
            plotter.disconnect()
            tvStatus.text = "Отключено"
            btnConnect.isEnabled = true
            btnDisconnect.isEnabled = false
            log("Отключён от плоттера")
        }

        btnOpenWeb.setOnClickListener {
            // Открываем локальный интерфейс в браузере телефона
            val url = "http://127.0.0.1:8000"
            log("Открытие интерфейса: $url")
            val intent = android.content.Intent(android.content.Intent.ACTION_VIEW)
            intent.data = android.net.Uri.parse(url)
            startActivity(intent)
        }
    }

    private fun log(msg: String) {
        runOnUiThread {
            tvLog.append("$msg\n")
            tvLog.scrollTo(0, tvLog.lineCount)
        }
    }

    override fun onDestroy() {
        plotter.disconnect()
        try { webServer.stop() } catch (_: Exception) {}
        super.onDestroy()
    }
}
