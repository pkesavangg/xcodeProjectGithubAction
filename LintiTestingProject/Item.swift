//
//  Item.swift
//  LintiTestingProject
//
//  Created by Kesavan Panchabakesan on 21/01/26.
//

import Foundation
import SwiftData

@Model
final class Item {
    var timestamp: Date
    
    init(timestamp: Date) {
        self.timestamp = timestamp
    }
}
