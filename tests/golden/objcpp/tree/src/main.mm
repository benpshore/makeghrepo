#import <Foundation/Foundation.h>
#include <string>

int main() {
    @autoreleasepool {
        std::string name = "quiet-otter";
        NSLog(@"Hello from %s!", name.c_str());
    }
    return 0;
}
