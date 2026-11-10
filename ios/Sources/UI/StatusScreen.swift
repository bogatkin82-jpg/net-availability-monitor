import SwiftUI

/// Экран «Статус»: сводная плашка, карточки сервисов, раскрываемые проверки.
struct StatusScreen: View {
    @EnvironmentObject private var store: MonitorStore
    @State private var expandedIds: Set<String> = []

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    SummaryCard(
                        snapshot: store.snapshot,
                        config: store.config,
                        isChecking: store.isChecking
                    ) {
                        Task { await store.checkNow() }
                    }

                    ForEach(store.snapshot.services) { service in
                        ServiceCard(
                            service: service,
                            config: store.config,
                            isExpanded: binding(for: service.id)
                        )
                    }

                    FooterCard(
                        snapshot: store.snapshot,
                        nextCheckDate: store.nextCheckDate,
                        source: store.configSource
                    )
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)
                .padding(.bottom, 28)
            }
            .background(Theme.screenBackground)
            .navigationTitle("NetMonitor")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await store.checkNow() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(store.isChecking)
                    .accessibilityLabel("Проверить сейчас")
                }
            }
            .refreshable {
                await store.checkNow()
            }
        }
    }

    private func binding(for serviceId: String) -> Binding<Bool> {
        Binding(
            get: { expandedIds.contains(serviceId) },
            set: { expanded in
                if expanded {
                    expandedIds.insert(serviceId)
                } else {
                    expandedIds.remove(serviceId)
                }
            }
        )
    }
}
