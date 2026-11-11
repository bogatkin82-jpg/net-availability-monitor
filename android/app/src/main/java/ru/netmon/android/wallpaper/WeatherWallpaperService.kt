package ru.netmon.android.wallpaper

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import android.os.Handler
import android.os.Looper
import android.service.wallpaper.WallpaperService
import android.view.SurfaceHolder
import ru.netmon.android.widget.WeatherRepository
import kotlin.math.cos
import kotlin.math.sin
import kotlin.random.Random

/**
 * Живой фон: анимированная погода (как в системном виджете HONOR).
 * Данные берутся из WeatherRepository (те же, что у виджета).
 */
class WeatherWallpaperService : WallpaperService() {

    override fun onCreateEngine(): Engine = WeatherEngine()

    private inner class WeatherEngine : Engine() {
        private val handler = Handler(Looper.getMainLooper())
        private val random = Random(System.currentTimeMillis())
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG)

        private var visible = false
        private var width = 1f
        private var height = 1f
        private var lastFrameNs = 0L
        private var background: Shader? = null

        private val drops = ArrayList<Drop>()
        private val flakes = ArrayList<Flake>()
        private val stars = ArrayList<Star>()
        private val clouds = ArrayList<Cloud>()

        private val frame = object : Runnable {
            override fun run() {
                drawFrame()
                if (visible) handler.postDelayed(this, 33L)
            }
        }

        override fun onVisibilityChanged(visible: Boolean) {
            this.visible = visible
            handler.removeCallbacks(frame)
            if (visible) {
                WeatherRepository.refreshIfNeeded(applicationContext)
                lastFrameNs = System.nanoTime()
                frame.run()
            }
        }

        override fun onSurfaceDestroyed(holder: SurfaceHolder) {
            visible = false
            handler.removeCallbacks(frame)
            super.onSurfaceDestroyed(holder)
        }

        override fun onSurfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
            this.width = width.toFloat().coerceAtLeast(1f)
            this.height = height.toFloat().coerceAtLeast(1f)
            background = LinearGradient(
                0f, 0f, 0f, this.height,
                intArrayOf(Color.rgb(7, 17, 32), Color.rgb(11, 27, 46), Color.rgb(6, 10, 18)),
                floatArrayOf(0f, 0.55f, 1f), Shader.TileMode.CLAMP,
            )
            seed()
        }

        private fun seed() {
            drops.clear(); flakes.clear(); stars.clear(); clouds.clear()
            repeat(120) {
                drops += Drop(
                    random.nextFloat() * width,
                    random.nextFloat() * height,
                    500f + random.nextFloat() * 700f,
                    18f + random.nextFloat() * 26f,
                )
            }
            repeat(90) {
                flakes += Flake(
                    random.nextFloat() * width,
                    random.nextFloat() * height,
                    18f + random.nextFloat() * 45f,
                    1.5f + random.nextFloat() * 3.5f,
                    random.nextFloat() * 6.28f,
                )
            }
            repeat(70) {
                stars += Star(
                    random.nextFloat() * width,
                    random.nextFloat() * height * 0.7f,
                    1f + random.nextFloat() * 2.2f,
                    random.nextFloat() * 6.28f,
                )
            }
            repeat(7) {
                clouds += Cloud(
                    random.nextFloat() * width,
                    height * (0.12f + random.nextFloat() * 0.45f),
                    90f + random.nextFloat() * 180f,
                    6f + random.nextFloat() * 16f,
                )
            }
        }

        private fun drawFrame() {
            val holder = surfaceHolder
            val canvas = holder.lockCanvas() ?: return
            try {
                val now = System.nanoTime()
                val dt = if (lastFrameNs == 0L) 0.016f else
                    ((now - lastFrameNs) / 1_000_000_000.0).toFloat().coerceIn(0.001f, 0.05f)
                lastFrameNs = now
                drawBackground(canvas)
                val weather = WeatherRepository.cached
                val code = weather?.weatherCode ?: -1
                val isDay = weather?.isDay ?: true
                when {
                    code == 0 && !isDay -> drawStars(canvas, dt)
                    code == 0 -> drawSun(canvas)
                    code in 1..3 -> { drawStars(canvas, dt * 0.3f); drawClouds(canvas, dt) }
                    code == 45 || code == 48 -> drawFog(canvas, dt)
                    code in 71..77 || code in 85..86 -> drawSnow(canvas, dt)
                    code in 95..99 -> { drawRain(canvas, dt); drawClouds(canvas, dt); drawLightning(canvas, now) }
                    code in -1..0 -> { drawClouds(canvas, dt) }
                    else -> { drawRain(canvas, dt); drawClouds(canvas, dt) }
                }
            } finally {
                holder.unlockCanvasAndPost(canvas)
            }
        }

        private fun drawBackground(canvas: Canvas) {
            paint.shader = background
            paint.alpha = 255
            canvas.drawPaint(paint)
            paint.shader = null
        }

        private fun drawStars(canvas: Canvas, dt: Float) {
            paint.color = Color.WHITE
            for (star in stars) {
                star.phase += dt * 1.5f
                paint.alpha = (60 + 120 * (0.5f + 0.5f * sin(star.phase))).toInt().coerceIn(0, 255)
                canvas.drawCircle(star.x, star.y, star.r, paint)
            }
        }

        private fun drawSun(canvas: Canvas) {
            paint.color = Color.rgb(255, 214, 130)
            paint.alpha = 55
            canvas.drawCircle(width * 0.78f, height * 0.18f, width * 0.20f, paint)
            paint.alpha = 120
            canvas.drawCircle(width * 0.78f, height * 0.18f, width * 0.09f, paint)
        }

        private fun drawClouds(canvas: Canvas, dt: Float) {
            paint.color = Color.WHITE
            for (cloud in clouds) {
                cloud.x += cloud.speed * dt
                if (cloud.x - cloud.r > width) cloud.x = -cloud.r
                paint.alpha = 28
                canvas.drawCircle(cloud.x, cloud.y, cloud.r, paint)
                canvas.drawCircle(cloud.x + cloud.r * 0.75f, cloud.y + cloud.r * 0.15f, cloud.r * 0.72f, paint)
                canvas.drawCircle(cloud.x - cloud.r * 0.7f, cloud.y + cloud.r * 0.22f, cloud.r * 0.62f, paint)
            }
        }

        private fun drawRain(canvas: Canvas, dt: Float) {
            paint.color = Color.rgb(160, 205, 255)
            paint.strokeWidth = 2f
            paint.alpha = 130
            for (drop in drops) {
                drop.y += drop.speed * dt
                if (drop.y > height) {
                    drop.y = -drop.length
                    drop.x = random.nextFloat() * width
                }
                canvas.drawLine(drop.x, drop.y, drop.x - 4f, drop.y + drop.length, paint)
            }
        }

        private fun drawSnow(canvas: Canvas, dt: Float) {
            paint.color = Color.WHITE
            paint.alpha = 190
            for (flake in flakes) {
                flake.phase += dt * 1.8f
                flake.y += flake.speed * dt
                flake.x += sin(flake.phase) * 16f * dt
                if (flake.y > height) {
                    flake.y = -flake.r * 3
                    flake.x = random.nextFloat() * width
                }
                canvas.drawCircle(flake.x, flake.y, flake.r, paint)
            }
        }

        private fun drawFog(canvas: Canvas, dt: Float) {
            paint.color = Color.WHITE
            paint.alpha = 22
            for (cloud in clouds) {
                cloud.x += cloud.speed * dt * 0.4f
                if (cloud.x - cloud.r * 2 > width) cloud.x = -cloud.r * 2
                canvas.drawOval(
                    cloud.x - cloud.r * 2f, cloud.y - cloud.r * 0.25f,
                    cloud.x + cloud.r * 2f, cloud.y + cloud.r * 0.25f, paint,
                )
            }
        }

        private fun drawLightning(canvas: Canvas, now: Long) {
            val phase = (now / 1_000_000_000.0) % 7.0
            if (phase < 0.12) {
                paint.color = Color.WHITE
                paint.alpha = 60
                canvas.drawPaint(paint)
            }
        }
    }

    private data class Drop(var x: Float, var y: Float, val speed: Float, val length: Float)
    private data class Flake(var x: Float, var y: Float, val speed: Float, val r: Float, var phase: Float)
    private data class Star(val x: Float, val y: Float, val r: Float, var phase: Float)
    private data class Cloud(var x: Float, val y: Float, val r: Float, val speed: Float)
}
