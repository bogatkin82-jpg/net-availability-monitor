package ru.netmon.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import ru.netmon.android.ui.NetMonRoot
import ru.netmon.android.ui.NetMonTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as NetMonApp).container
        setContent {
            NetMonTheme {
                NetMonRoot(container)
            }
        }
    }
}
