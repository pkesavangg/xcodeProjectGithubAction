//
//  ContentView.swift
//  LintiTestingProject
//
//  Created by Kesavan Panchabakesan on 21/01/26.
//

import SwiftData
import SwiftUI

struct ContentView: View {
    @Environment(\.modelContext) private var modelContext
    @Query private var items: [Item]
    @State private var value: Int = 0
    
    var body: some View {
        Text("Select an item")
            .gesture(
                DragGesture()
                    .onChanged { value in
                        self.value = Int(value.translation.width)
                    }
            )
    }

    private func addItem() {
        withAnimation {
            let newItem = Item(timestamp: Date())
            modelContext.insert(newItem)
        }
    }

    private func deleteItems(offsets: IndexSet) {
        withAnimation {
            for index in offsets {
                modelContext.delete(items[index])
            }
        }
    }
}

#Preview {
    ContentView()
        .modelContainer(for: Item.self, inMemory: true)
}
