package com.guangku.plotter

import android.content.Context
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import com.hoho.android.usbserial.driver.UsbSerialDriver
import com.hoho.android.usbserial.driver.UsbSerialPort
import com.hoho.android.usbserial.driver.UsbSerialProber
import com.hoho.android.usbserial.util.SerialInputOutputManager
import java.util.concurrent.Executors
import java.util.concurrent.LinkedBlockingQueue
import kotlin.concurrent.thread

/**
 * Обёртка над usb-serial-for-android для связи с GRBL-плоттером через USB-OTG.
 *
 * GRBL приёмыш:
 *   - принимает G-команды по одной строке, завершённой \n
 *   - отвечает "ok" после выполнения команды
 *   - кольцевой буфер incoming ~128 байт (GRBL v1.1) — не слать много команд без подтверждения
 *   - '!' = feed hold (пауза), '~' = resume, 'X' = сброс, '?' = статус, '$H' = homing
 */
class UsbPlotter(private val context: Context) {

    private val usbManager: UsbManager = context.getSystemService(Context.USB_SERVICE) as UsbManager
    private var serialPort: UsbSerialPort? = null
    private var ioManager: SerialInputOutputManager? = null
    private val executor = Executors.newSingleThreadExecutor()

    // очередь команд на отправку ( GRBL-friendly: не более maxInFlight без 'ok' )
    private val sendQueue = LinkedBlockingQueue<String>()
    private val maxInFlight = 4
    private var inFlight = 0
    private var pendingAck = java.util.concurrent.CountDownLatch(1)

    private var connected = false
    private var listener: ((String) -> Unit)? = null

    /** Подключиться к первому найденному USB-Seial устройству (плоттеру) */
    fun connect(): Boolean {
        val driver = UsbSerialProber.acquire(usbManager)
        if (driver == null) return false

        val port = driver.devicePorts[0]
        serialPort = port

        // запрашиваем разрешение у пользователя (если ещё не дано)
        val permissionIntent = android.app.PendingIntent.getBroadcast(
            context, 0,
            android.content.Intent("com.guangku.plotter.USB_PERMISSION"),
            android.app.PendingIntent.FLAG_IMMUTABLE
        )
        if (!usbManager.hasPermission(driver.device)) {
            usbManager.requestPermission(driver.device, permissionIntent)
            // в реальном приложении нужно дождаться broadcast результата;
            // для упрощения пробуем открыть сразу
        }

        try {
            port.open(usbManager)
            port.setParameters(115200, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE)
            // GRBL перезагружается при открытии порта — ждём
            Thread.sleep(2000)
            port.purgeHwBuffers()
        } catch (e: Exception) {
            serialPort = null
            return false
        }

        connected = true
        startIoManager(port)
        thread(name = "plotter-sender") {
            runSenderLoop()
        }
        log("Подключён к плоттеру: ${driver.device.deviceName}")
        return true
    }

    private fun startIoManager(port: UsbSerialPort) {
        ioManager = SerialInputOutputManager(port, object : SerialInputOutputManager.Listener {
            override fun onRunError(e: Exception) {
                log("Ошибка чтения USB: $e")
            }
            override fun onNewData(data: ByteArray) {
                val s = String(data).trim()
                if (s.isNotEmpty()) {
                    log("GRBL: $s")
                    if (s == "ok") {
                        inFlight = (inFlight - 1).coerceAtLeast(0)
                        pendingAck.countDown()
                    }
                }
            }
        })
        executor.submit(ioManager!!)
    }

    private fun runSenderLoop() {
        while (connected) {
            try {
                val cmd = sendQueue.poll(100, java.util.concurrent.TimeUnit.MILLISECONDS) ?: continue
                if (!connected) break

                serialPort?.write(cmd.toByteArray(), 1000)
                serialPort?.write("\n".toByteArray(), 1000)

                inFlight++
                pendingAck = java.util.concurrent.CountDownLatch(1)

                // если накопилось слишком много未подтверждённых — ждём 'ok'
                if (inFlight >= maxInFlight) {
                    pendingAck.await(10, java.util.concurrent.TimeUnit.SECONDS)
                }
            } catch (e: Exception) {
                log("Ошибка отправки: $e")
            }
        }
    }

    /** Отправить команду в очередь (не блокирующий вызов) */
    fun send(cmd: String) {
        if (!connected) {
            log("Не подключено к плоттеру")
            return
        }
        sendQueue.offer(cmd.trim())
    }

    /** Отправить и дождаться подтверждения 'ok' */
    fun sendAndWait(cmd: String, timeoutSec: Int = 5): Boolean {
        if (!connected) return false
        sendQueue.offer(cmd.trim())
        return pendingAck.await(timeoutSec, java.util.concurrent.TimeUnit.SECONDS)
    }

    fun disconnect() {
        connected = false
        try { ioManager?.setListener(null) } catch (_: Exception) {}
        try { ioManager?.stop() } catch (_: Exception) {}
        try { serialPort?.close() } catch (_: Exception) {}
        serialPort = null
        ioManager = null
        log("Отключён от плоттера")
    }

    fun isConnected(): Boolean = connected

    // ---------- командные сокращения для GRBL ----------
    fun home() => send("$H")
    fun zero() => send("G92 X0 Y0")
    fun pause() => send("!")
    fun resume() => send("~")
    fun stop() => send("X")
    fun jog(axis: String, mm: Double, feed: Int = 1500) => send("$J=G91 ${axis}${mm.toString()} F$feed")
    fun penDown() => send("G1 Z-1.5 F600")   // значения по умолчанию; можно вынести в настройки
    fun penUp() => send("G1 Z3.0 F600")
    fun status() => send("?")

    private fun log(msg: String) {
        listener?.invoke(msg)
        android.util.Log.d("GuangkuPlotter", msg)
    }

    fun setListener(l: (String) -> Unit) { listener = l }
}
